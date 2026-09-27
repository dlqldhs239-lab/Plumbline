"""Voting and comment rules. Views and API call these; nothing else writes
votes or comments."""

from __future__ import annotations

import hashlib
import random
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from audit.services import record
from events.models import Event, Project
from events.permissions import is_organizer
from judging.services import eligible_projects
from plumbline.inputs import as_int, client_ip

from .models import Comment, Vote, Voter

signer = Signer(salt="plumbline.voter")
COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def cookie_name(event: Event) -> str:
    return f"pl_voter_{event.pk}"


def _hash(value: str) -> str:
    return hashlib.sha256((settings.SECRET_KEY + "|" + (value or "")).encode()).hexdigest()


class Throttled(PermissionDenied):
    pass


def throttle(request, bucket: str, limit: int | None = None, window: int = 60):
    """Fixed-window rate limit per client IP, shared across workers through
    the database cache. Raises Throttled when exceeded."""
    limit = limit or settings.PLUMBLINE_ANON_WRITE_RATE
    key = f"throttle:{bucket}:{_hash(client_ip(request))[:24]}"
    if cache.add(key, 1, timeout=window):
        return
    try:
        count = cache.incr(key)
    except ValueError:
        # The window ended between the two calls; this request opens the next one.
        cache.add(key, 1, timeout=window)
        return
    if count > limit:
        raise Throttled("Too many requests from this address. Try again in a minute.")


# --- voters -------------------------------------------------------------------


def voter_from_request(request, event: Event) -> Voter | None:
    """The voter this request belongs to, or None. Never creates one."""
    if event.voting_access == Event.VotingAccess.AUTHENTICATED:
        if not request.user.is_authenticated:
            return None
        return Voter.objects.filter(event=event, user=request.user).first()
    # Open mode: an account holds one ballot however it arrives (browser or
    # API token), so a signed-in caller is matched by account first.
    if event.voting_access == Event.VotingAccess.OPEN and request.user.is_authenticated:
        mine = Voter.objects.filter(event=event, user=request.user).first()
        if mine is not None:
            return mine
    raw = request.COOKIES.get(cookie_name(event))
    if not raw:
        return None
    try:
        key = signer.unsign(raw)
    except BadSignature:
        return None
    voter = Voter.objects.filter(event=event, key=key).first()
    if voter is not None and voter.user_id and voter.user_id != getattr(request.user, "id", None):
        return None  # a ballot bound to an account is not used by anyone else
    return voter


@transaction.atomic
def admit_voter(request, event: Event, ballot_token: str | None = None) -> Voter:
    """Create or fetch the voter for this request according to the event's
    access mode. Returns the voter; the caller sets the cookie via attach_cookie."""
    if not event.voting_open():
        raise PermissionDenied("Voting is not open.")
    mode = event.voting_access
    ip_hash = _hash(client_ip(request))
    ua_hash = _hash(request.META.get("HTTP_USER_AGENT", ""))[:64]
    if mode == Event.VotingAccess.AUTHENTICATED:
        if not request.user.is_authenticated:
            raise PermissionDenied("Sign in to vote in this event.")
        voter, created = Voter.objects.get_or_create(
            event=event, user=request.user, defaults={"kind": Voter.Kind.AUTH, "ip_hash": ip_hash, "ua_hash": ua_hash}
        )
    elif mode == Event.VotingAccess.EMAIL:
        if not ballot_token:
            existing = voter_from_request(request, event)
            if existing is None:
                raise PermissionDenied("This event needs a personal ballot link.")
            return existing
        voter = Voter.objects.filter(event=event, kind=Voter.Kind.EMAIL, ballot_token=ballot_token).first()
        if voter is None:
            raise PermissionDenied("That ballot link is not valid.")
        created = False
        if not voter.ip_hash:
            voter.ip_hash, voter.ua_hash = ip_hash, ua_hash
    elif mode == Event.VotingAccess.OPEN:
        existing = voter_from_request(request, event)
        if existing is not None:
            if request.user.is_authenticated and existing.user_id is None:
                # Signed in after opening the ballot: the ballot becomes theirs.
                Voter.objects.filter(pk=existing.pk, user__isnull=True).update(user=request.user)
                existing.user = request.user
            return existing
        throttle(request, f"admit:{event.pk}", limit=10)
        if request.user.is_authenticated:
            # Without this an API caller, who carries no cookie, would be
            # handed a new ballot and a new budget on every request.
            get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()
            taken = Voter.objects.filter(event=event, user=request.user).first()
            if taken is not None:
                return taken
        voter = Voter(event=event, kind=Voter.Kind.OPEN, ip_hash=ip_hash, ua_hash=ua_hash)
        if request.user.is_authenticated:
            voter.user = request.user
        created = True
        _flag_duplicates(voter)
    else:
        raise PermissionDenied("Voting is closed for this event.")
    voter.last_seen_at = timezone.now()
    voter.save()
    if created:
        record(
            "voter.admit",
            event=event,
            target=voter,
            detail={"kind": voter.kind, "flags": voter.flags},
            actor=request.user if request.user.is_authenticated else None,
        )
    return voter


def _flag_duplicates(voter: Voter):
    """Same IP and browser already holding open ballots is the cheapest
    ballot-stuffing signal. Flag, do not block: shared offices exist. The
    organizer decides on the voting integrity page."""
    twins = Voter.objects.filter(event=voter.event, kind=Voter.Kind.OPEN, ip_hash=voter.ip_hash).count()
    if twins >= 1:
        voter.flags = list({*voter.flags, "shared_ip"})
    if twins >= 3:
        voter.flags = list({*voter.flags, "many_ballots_same_ip"})


def attach_cookie(response, event: Event, voter: Voter):
    if voter.kind != Voter.Kind.AUTH:
        response.set_cookie(
            cookie_name(event), signer.sign(voter.key), max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax"
        )
    return response


@transaction.atomic
def create_email_voters(event: Event, user, emails: list[str]) -> list[Voter]:
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can issue ballot links.")
    created = []
    for raw in emails:
        email = raw.strip().lower()
        if not email or "@" not in email:
            continue
        voter, was_created = Voter.objects.get_or_create(
            event=event, email=email, defaults={"kind": Voter.Kind.EMAIL, "ballot_token": secrets.token_urlsafe(24)}
        )
        if was_created:
            created.append(voter)
    record("voter.issue_links", actor=user, event=event, detail={"requested": len(emails), "created": len(created)})
    return created


@transaction.atomic
def void_voter(voter: Voter, user, reason: str = "") -> Voter:
    if not is_organizer(user, voter.event):
        raise PermissionDenied("Only organizers can void ballots.")
    voter.voided_at = timezone.now()
    voter.void_reason = reason[:200]
    voter.save(update_fields=["voided_at", "void_reason"])
    record(
        "voter.void",
        actor=user,
        event=voter.event,
        target=voter,
        detail={"reason": reason, "votes": voter.votes.count()},
    )
    return voter


# --- ballot ---------------------------------------------------------------------


def ballot_projects(event: Event, voter_key: str) -> list[Project]:
    """Eligible projects in an order that is random per voter but stable
    across reloads, so nobody benefits from being listed first."""
    projects = list(eligible_projects(event).select_related("team", "track"))
    rng = random.Random(hashlib.sha256(f"{event.pk}:{voter_key}".encode()).hexdigest())
    rng.shuffle(projects)
    return projects


def voter_weights(voter: Voter | None) -> dict[int, int]:
    if voter is None:
        return {}
    return dict(voter.votes.values_list("project_id", "weight"))


@transaction.atomic
def cast_vote(request, event: Event, voter: Voter, project: Project, weight: int) -> Vote | None:
    """Set this voter's weight on a project. weight 0 removes the vote."""
    if not event.voting_open():
        raise PermissionDenied("Voting is closed.")
    if voter.is_voided:
        raise PermissionDenied("This ballot has been voided.")
    if voter.event_id != event.id:
        raise PermissionDenied("That ballot belongs to a different event.")
    if project.event_id != event.id or not project.is_public or project.duplicate_of_id:
        raise ValidationError("That project is not on the ballot.")
    throttle(request, f"vote:{event.pk}")
    weight = as_int(weight, "Weight", 0, 1000)
    # Two requests from one ballot are counted one after the other, so the
    # budget holds when they arrive together.
    Voter.objects.select_for_update().filter(pk=voter.pk).first()
    if event.voting_credits:
        others = sum(v.weight**2 for v in voter.votes.exclude(project=project))
        if others + weight * weight > event.voting_credits:
            raise ValidationError(
                f"Not enough credits: {weight} votes cost {weight * weight}, you have {event.voting_credits - others} left."
            )
    elif weight > 1:
        raise ValidationError("This event allows one vote per project.")
    existing = Vote.objects.filter(voter=voter, project=project).first()
    if weight == 0:
        if existing:
            existing.delete()
            record(
                "vote.remove",
                event=event,
                target=project,
                detail={"voter": voter.key[:8]},
                actor=request.user if request.user.is_authenticated else None,
            )
        return None
    vote, created = Vote.objects.update_or_create(
        voter=voter, project=project, defaults={"event": event, "weight": weight}
    )
    record(
        "vote.cast" if created else "vote.change",
        event=event,
        target=project,
        detail={"voter": voter.key[:8], "weight": weight},
        actor=request.user if request.user.is_authenticated else None,
    )
    Voter.objects.filter(pk=voter.pk).update(last_seen_at=timezone.now())
    return vote


def tally(event: Event) -> dict[int, dict]:
    """Community totals per project, excluding voided ballots."""
    rows = (
        Vote.objects.filter(event=event, voter__voided_at__isnull=True)
        .values("project_id")
        .annotate(votes=Sum("weight"), voters=Count("voter_id", distinct=True))
    )
    return {r["project_id"]: {"votes": r["votes"] or 0, "voters": r["voters"]} for r in rows}


def integrity_report(event: Event) -> dict:
    """What an organizer needs to spot abuse without a database client."""
    voters = Voter.objects.filter(event=event).annotate(n_votes=Count("votes"), weight=Sum("votes__weight"))
    by_ip = (
        Voter.objects.filter(event=event, kind=Voter.Kind.OPEN)
        .exclude(ip_hash="")
        .values("ip_hash")
        .annotate(ballots=Count("id"))
        .filter(ballots__gt=1)
        .order_by("-ballots")
    )
    return {
        "total": voters.count(),
        "voided": voters.filter(voided_at__isnull=False).count(),
        "flagged": voters.exclude(flags=[]).count(),
        # Counted on its own query: the vote join above would count a ballot
        # once per vote it holds.
        "by_kind": dict(
            Voter.objects.filter(event=event)
            .order_by()
            .values("kind")
            .annotate(n=Count("id", distinct=True))
            .values_list("kind", "n")
        ),
        "shared_ips": list(by_ip[:50]),
        "voters": voters.order_by("-n_votes", "-created_at"),
    }


# --- comments -------------------------------------------------------------------


@transaction.atomic
def add_comment(request, project: Project, body: str) -> Comment:
    if not request.user.is_authenticated:
        raise PermissionDenied("Sign in to comment.")
    if not project.event.comments_enabled:
        raise PermissionDenied("Comments are disabled for this event.")
    if not project.is_public:
        raise PermissionDenied("This project is not public.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("Write something first.")
    if len(body) > 2000:
        raise ValidationError("Comments are limited to 2000 characters.")
    throttle(request, f"comment:{project.event_id}", limit=10)
    last = Comment.objects.filter(project=project, author=request.user).order_by("-created_at").first()
    if last and last.body == body:
        raise ValidationError("You already posted exactly that.")
    comment = Comment.objects.create(project=project, author=request.user, body=body)
    record("comment.create", event=project.event, target=project, detail={"comment": comment.pk})
    return comment


@transaction.atomic
def hide_comment(comment: Comment, user, hidden: bool = True) -> Comment:
    if not is_organizer(user, comment.project.event):
        raise PermissionDenied("Only organizers can moderate comments.")
    if hidden:
        comment.hide(user)
    else:
        comment.hidden_at, comment.hidden_by = None, None
    comment.save(update_fields=["hidden_at", "hidden_by"])
    record(
        "comment.hide" if hidden else "comment.unhide",
        actor=user,
        event=comment.project.event,
        target=comment.project,
        detail={"comment": comment.pk},
    )
    return comment
