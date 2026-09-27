"""Issuing, revoking and checking signed records."""

from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from audit.services import record as audit
from events.models import Event, TeamMembership
from events.permissions import is_organizer
from judging.models import JudgeAssignment
from judging.services import ensure_rubric, placed
from plumbline.inputs import as_int

from .models import Record, new_serial

METHOD = "HMAC-SHA256"
# What two records must share to be the same statement. Serial and date are
# left out: issuing the same statement twice gives one record, not two.
IDENTITY = ("kind", "event", "recipient", "team", "project", "place", "of", "track", "score", "reviews")


def _key() -> bytes:
    """A key for records only, derived from the installation's secret, so a
    signature here can never be replayed as a session or a token."""
    return hashlib.sha256(("plumbline.records|" + settings.SECRET_KEY).encode()).digest()


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(payload: dict) -> str:
    return hmac.new(_key(), canonical(payload), hashlib.sha256).hexdigest()


def display_name(user) -> str:
    return (user.get_full_name() or user.get_username()).strip()


def check(payload, signature) -> dict:
    """Is this document one we issued, unchanged, and still standing?

    Answers with a state and a sentence. States: genuine, revoked, altered,
    unknown. A document is only genuine if the signature fits AND the record
    exists here: a signature alone would also fit a record from another
    installation that happens to share the secret."""
    not_a_record = {"state": "unknown", "says": "That is not a record: it needs a payload and a signature."}
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return not_a_record
    try:
        expected = sign(payload).encode("ascii")
        given = signature.strip().lower().encode("utf-8", "replace")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return not_a_record
    good = hmac.compare_digest(expected, given)
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
    if found.is_revoked:
        return {
            "state": "revoked",
            "says": f"This record was issued here and later withdrawn ({found.revoke_reason or 'no reason given'}).",
            "record": found,
        }
    resting = why_not_standing(found)
    if resting:
        return {"state": "suspended", "says": resting, "record": found}
    return {"state": "genuine", "says": "Issued here, unchanged, and standing.", "record": found}


def why_not_standing(rec: Record) -> str:
    """A record of a place rests on results that are published and on a
    project that is in them. If either is no longer so, the record is not
    withdrawn, because nobody decided that, but it does not stand either."""
    if rec.kind == Record.Kind.JUDGE:
        return ""
    if not rec.event.results_published:
        return "The results this record rests on are not published at present."
    project = rec.project
    if project is None or not project.is_public or project.duplicate_of_id:
        return "The project this record names is not in the published results at present."
    return ""


def state_of(rec: Record) -> dict:
    return check(rec.payload, rec.signature)


def _payload(kind: str, event: Event, user, issued_at, **facts) -> dict:
    payload = {
        "serial": new_serial(),
        "kind": kind,
        "event": {"slug": event.slug, "name": event.name},
        "recipient": display_name(user),
        "issued_at": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issuer": settings.PLUMBLINE_SITE_NAME,
        "signed_with": METHOD,
    }
    payload.update({k: v for k, v in facts.items() if v is not None})
    return payload


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in IDENTITY)


def _issue(kind, event, user, actor, subject=None, **facts) -> tuple[Record, bool]:
    """Create the record unless the same statement already stands. A standing
    record that says something else (the place changed after a recompute) is
    withdrawn and replaced, so there is never more than one to believe."""
    payload = _payload(kind, event, user, timezone.now(), **facts)
    standing = Record.objects.filter(
        event=event, recipient=user, project=subject, revoked_at__isnull=True, kind__in=_family(kind)
    )
    for old in standing:
        if _same(old.payload, payload):
            return old, False
    for old in standing:
        old.revoked_at = timezone.now()
        old.revoke_reason = "replaced by a newer record"
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
    out = {"issued": 0, "standing": 0, "teams": 0, "judges": 0, "withdrawn": 0}
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
        owed.add(rec.pk)
        out["issued" if created else "standing"] += 1

    # What stood before and is owed no longer: the project was hidden or
    # withdrawn, the member left the team, the judge's reviews were removed.
    # Without this, two teams could each hold a standing record of first place.
    stale = Record.objects.filter(event=event, revoked_at__isnull=True).exclude(pk__in=owed)
    out["withdrawn"] = stale.update(revoked_at=timezone.now(), revoke_reason="no longer in the published results")

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
    """What a holder keeps and a verifier checks."""
    return {"payload": rec.payload, "signature": rec.signature}
