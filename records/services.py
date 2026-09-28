"""Issuing, revoking and checking signed records."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.functional import cached_property

from audit.services import record as audit
from events.models import Event, TeamMembership
from events.permissions import is_organizer
from judging.models import JudgeAssignment
from judging.services import ensure_rubric, placed
from plumbline.inputs import as_int

from . import signing
from .models import Record, new_serial
from .signing import canonical, sign  # noqa: F401 - part of this module's face

METHOD = signing.ED25519
# What two records must share to be the same statement. Serial and date are
# left out: issuing the same statement twice gives one record, not two.
IDENTITY = ("kind", "event", "recipient", "team", "project", "place", "of", "track", "score", "reviews")
# Reasons the portal writes itself. Any other reason is an organizer's decision.
REPLACED = "replaced by a newer record"
NOT_IN_RESULTS = "no longer in the published results"
AUTOMATIC = (REPLACED, NOT_IN_RESULTS)


def display_name(user) -> str:
    return (user.get_full_name() or user.get_username()).strip()


class Ground:
    """What a record of this event rests on: the published places, who is on
    which team, the scale. Read once, however many records are checked."""

    def __init__(self, event: Event):
        self.event = event

    @cached_property
    def rows(self) -> dict:
        return {r.project_id: r for r in placed(self.event)}

    @cached_property
    def members(self) -> set:
        return set(TeamMembership.objects.filter(team__event=self.event).values_list("team_id", "user_id"))

    @cached_property
    def scale_max(self) -> int:
        return ensure_rubric(self.event).scale_max

    def score(self, row) -> str:
        return f"{row.adjusted_mean:.3f} / {self.scale_max}.00"


def check(payload, signature, ground: Ground | None = None) -> dict:
    """Is this document one we issued, unchanged, and still standing?

    Answers with a state and a sentence. States: genuine, revoked, suspended,
    altered, unknown. A document is only genuine if the signature fits AND
    the record exists here: a signature alone would also fit a record from
    another installation that happens to share the secret.

    `withheld` says whether what the record states may be shown. It may not
    while the record does not rest on the published results, withdrawn or
    not: it could name a place in results that are not public."""
    not_a_record = {"state": "unknown", "says": "That is not a record: it needs a payload and a signature."}
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return not_a_record
    try:
        canonical(payload)
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return not_a_record
    good = signing.fits(payload, signature)
    serial = payload.get("serial")
    found = Record.objects.filter(serial=serial.strip().upper()[:20]).first() if isinstance(serial, str) else None
    if found is None:
        return {"state": "unknown", "says": "No record with that number was issued here."}
    if not good or found.payload != payload or canonical(found.payload) != canonical(payload):
        return {
            "state": "altered",
            "says": "The signature does not fit this document. Something in it was changed after it was issued.",
            "record": found,
        }
    resting = why_not_standing(found, ground)
    if found.is_revoked:
        return {
            "state": "revoked",
            "says": f"This record was issued here and later withdrawn ({found.revoke_reason or 'no reason given'}).",
            "record": found,
            "withheld": bool(resting),
        }
    if resting:
        return {"state": "suspended", "says": resting, "record": found, "withheld": True}
    return {"state": "genuine", "says": "Issued here, unchanged, and standing.", "record": found, "withheld": False}


def why_not_standing(rec: Record, ground: Ground | None = None) -> str:
    """A record of a team rests on results that are published, on a project
    that is in them, on the person still being on that team, and on the
    place it states being the place the results give. If any of these is no
    longer so, the record is not withdrawn, because nobody decided that, but
    it does not stand either."""
    if rec.kind == Record.Kind.JUDGE:
        return ""
    if not rec.event.results_published:
        return "The results this record rests on are not published at present."
    project = rec.project
    if project is None or not project.is_public or project.duplicate_of_id:
        return "The project this record names is not in the published results at present."
    if ground is None or ground.event.pk != rec.event_id:
        ground = Ground(rec.event)
    row = ground.rows.get(project.id)
    if row is None or not row.place:
        return "The project this record names is not in the published results at present."
    if (project.team_id, rec.recipient_id) not in ground.members:
        return "The person this record names is not on that team at present."
    if rec.kind == Record.Kind.PLACEMENT:
        said = (rec.payload.get("place"), rec.payload.get("of"), rec.payload.get("score"))
        if said != (row.place, row.of, ground.score(row)):
            return "The published results have changed since this record was issued. It no longer states them."
    return ""


def state_of(rec: Record, ground: Ground | None = None) -> dict:
    return check(rec.payload, rec.signature, ground)


def _payload(kind: str, event: Event, user, issued_at, **facts) -> dict:
    payload = {
        "serial": new_serial(),
        "kind": kind,
        "event": {"slug": event.slug, "name": event.name},
        "recipient": display_name(user),
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issuer": settings.PLUMBLINE_SITE_NAME,
        "signed_with": METHOD,
        "key": signing.fingerprint(),
    }
    payload.update({k: v for k, v in facts.items() if v is not None})
    return payload


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in IDENTITY)


def _issue(kind, event, user, actor, subject=None, **facts) -> tuple[Record | None, bool]:
    """Create the record unless the same statement already stands. A standing
    record that says something else (the place changed after a recompute) is
    withdrawn and replaced, so there is never more than one to believe.

    A statement an organizer withdrew by hand is not made again: the answer
    is then no record at all."""
    payload = _payload(kind, event, user, timezone.now(), **facts)
    family = Record.objects.filter(event=event, recipient=user, project=subject, kind__in=_family(kind))
    standing = family.filter(revoked_at__isnull=True)
    for old in standing:
        if _same(old.payload, payload):
            return old, False
    for old in family.filter(revoked_at__isnull=False).exclude(revoke_reason__in=AUTOMATIC):
        if _same(old.payload, payload):
            return None, False
    for old in standing:
        old.revoked_at = timezone.now()
        old.revoke_reason = REPLACED
        old.save(update_fields=["revoked_at", "revoke_reason"])
    rec = Record.objects.create(
        serial=payload["serial"],
        kind=kind,
        event=event,
        recipient=user,
        project=subject,
        payload=payload,
        signature=sign(payload),
        issued_by=actor,
    )
    return rec, True


def _family(kind: str) -> list[str]:
    """Placement and participation replace each other; judging stands alone."""
    if kind == Record.Kind.JUDGE:
        return [Record.Kind.JUDGE]
    return [Record.Kind.PLACEMENT, Record.Kind.PARTICIPATION]


@transaction.atomic
def issue_for_event(event: Event, actor, places: int = 3) -> dict:
    """Issue every record the event owes: one to each member of each ranked
    team (a placement for the first `places`, participation for the rest) and
    one to each judge who submitted at least one review. Safe to run again."""
    if not is_organizer(actor, event):
        raise PermissionDenied("Only organizers can issue records.")
    if not event.results_published:
        raise ValidationError("Publish the results first. A record states a place, and places are not final before.")
    places = as_int(places, "Placements", 0, 100)
    rubric = ensure_rubric(event)
    out = {"issued": 0, "standing": 0, "teams": 0, "judges": 0, "withdrawn": 0, "held_back": 0}
    owed: set[int] = set()
    members: dict[int, list] = {}
    for m in TeamMembership.objects.filter(team__event=event).select_related("user"):
        members.setdefault(m.team_id, []).append(m.user)

    for row in placed(event):
        if not row.place:
            continue
        project = row.project
        out["teams"] += 1
        kind = Record.Kind.PLACEMENT if row.place <= places else Record.Kind.PARTICIPATION
        for user in members.get(project.team_id, []):
            facts = {"team": project.team.name, "project": project.title}
            if project.track_id:
                facts["track"] = project.track.name
            if kind == Record.Kind.PLACEMENT:
                facts.update(
                    place=row.place,
                    of=row.of,
                    score=f"{row.adjusted_mean:.3f} / {rubric.scale_max}.00",
                    reviews=row.review_count,
                )
            rec, created = _issue(kind, event, user, actor, subject=project, **facts)
            if rec is None:
                out["held_back"] += 1
                continue
            owed.add(rec.pk)
            out["issued" if created else "standing"] += 1

    done = (
        JudgeAssignment.objects.filter(event=event, status=JudgeAssignment.Status.SUBMITTED)
        .values("judge_id")
        .annotate(n=Count("id"))
    )
    from django.contrib.auth import get_user_model

    judges = {u.id: u for u in get_user_model().objects.filter(id__in=[d["judge_id"] for d in done])}
    for d in done:
        out["judges"] += 1
        # A judge's record says that they judged and how much. Never what they scored.
        rec, created = _issue(Record.Kind.JUDGE, event, judges[d["judge_id"]], actor, reviews=d["n"])
        if rec is None:
            out["held_back"] += 1
            continue
        owed.add(rec.pk)
        out["issued" if created else "standing"] += 1

    # What stood before and is owed no longer: the project was hidden or
    # withdrawn, the member left the team, the judge's reviews were removed.
    # Without this, two teams could each hold a standing record of first place.
    stale = Record.objects.filter(event=event, revoked_at__isnull=True).exclude(pk__in=owed)
    out["withdrawn"] = stale.update(revoked_at=timezone.now(), revoke_reason=NOT_IN_RESULTS)

    audit("records.issue", actor=actor, event=event, detail=out)
    return out


@transaction.atomic
def revoke(rec: Record, actor, reason: str = "") -> Record:
    if not is_organizer(actor, rec.event):
        raise PermissionDenied("Only organizers can withdraw records.")
    if rec.is_revoked:
        raise ValidationError("That record is already withdrawn.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Say why. The reason is shown to anyone who checks the record.")
    rec.revoked_at = timezone.now()
    rec.revoke_reason = reason[:200]
    rec.save(update_fields=["revoked_at", "revoke_reason"])
    audit("records.revoke", actor=actor, event=rec.event, target=rec, detail={"reason": rec.revoke_reason})
    return rec


def document(rec: Record) -> dict:
    """What a holder keeps and a verifier checks. The public key is printed
    for convenience; a careful verifier takes it from the issuer instead."""
    out = {"payload": rec.payload, "signature": rec.signature}
    if rec.payload.get("signed_with") == signing.ED25519:
        out["public_key"] = signing.public_key()
        out["how_to_check"] = "python tools/verify_record.py <this file> --key <the issuer's public key>"
    return out
