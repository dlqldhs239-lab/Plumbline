"""Write operations for events, teams and projects.

Views and API endpoints call these; nothing else mutates state. Each function
enforces the rule it is named after and writes an audit entry.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator, validate_email
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from audit.services import record
from plumbline.inputs import as_id, as_int

from .models import Event, EventRole, Project, Role, Team, TeamInvite, TeamMembership, Track
from .permissions import is_organizer, is_team_member

# Slugs that would hide a page of the portal itself (/events/new/).
RESERVED_SLUGS = {"new"}
MAX_REVIEWS_PER_PROJECT = 20


class DeadlinePassed(PermissionDenied):
    pass


def _lock_user(user):
    """Serialise the rules that are counted per person (one team per event).
    A row lock on PostgreSQL; SQLite serialises writers by itself."""
    get_user_model().objects.select_for_update().filter(pk=user.pk).first()


def ensure_participant(user, event: Event) -> EventRole:
    role, _ = EventRole.objects.get_or_create(event=event, user=user, role=Role.PARTICIPANT)
    return role


@transaction.atomic
def create_team(event: Event, user, name: str) -> Team:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Team name is required.")
    if event.submissions_closed() and not is_organizer(user, event):
        raise DeadlinePassed("Submissions for this event have closed; teams are frozen.")
    _lock_user(user)
    if TeamMembership.objects.filter(team__event=event, user=user).exists():
        raise ValidationError("You are already on a team for this event.")
    if Team.objects.filter(event=event, name__iexact=name).exists():
        raise ValidationError("A team with that name already exists in this event.")
    team = Team(event=event, name=name, created_by=user)
    team.full_clean(exclude=["external_id"])
    team.save()
    TeamMembership.objects.create(team=team, user=user, role=TeamMembership.MemberRole.OWNER)
    ensure_participant(user, event)
    record("team.create", actor=user, event=event, target=team)
    return team


@transaction.atomic
def create_invite(team: Team, user, max_uses: int = 10, ttl_hours: int = 72) -> TeamInvite:
    if not is_team_member(user, _proxy(team)) and not is_organizer(user, team.event):
        raise PermissionDenied("Only team members can create invite links.")
    max_uses = as_int(max_uses, "max_uses", 1, 1000)
    ttl_hours = as_int(ttl_hours, "ttl_hours", 1, 24 * 90)
    invite = TeamInvite.objects.create(
        team=team,
        created_by=user,
        max_uses=max_uses,
        expires_at=timezone.now() + timedelta(hours=ttl_hours),
    )
    record("team.invite.create", actor=user, event=team.event, target=team, detail={"invite": invite.pk})
    return invite


@transaction.atomic
def join_team(invite: TeamInvite, user) -> TeamMembership:
    invite = TeamInvite.objects.select_for_update().get(pk=invite.pk)
    if not invite.is_valid():
        raise ValidationError("This invite link is no longer valid.")
    team = invite.team
    if team.event.submissions_closed():
        raise DeadlinePassed("Submissions for this event have closed; teams are frozen.")
    _lock_user(user)
    if TeamMembership.objects.filter(team=team, user=user).exists():
        return TeamMembership.objects.get(team=team, user=user)
    if TeamMembership.objects.filter(team__event=team.event, user=user).exists():
        raise ValidationError("You are already on another team for this event.")
    membership = TeamMembership.objects.create(team=team, user=user)
    invite.uses += 1
    invite.save(update_fields=["uses"])
    ensure_participant(user, team.event)
    record("team.join", actor=user, event=team.event, target=team, detail={"invite": invite.pk})
    return membership


def _proxy(team: Team):
    """Small shim so is_team_member can be reused with a team."""

    class _P:
        pass

    p = _P()
    p.team = team
    return p


PROJECT_FIELDS = (
    "title",
    "tagline",
    "description",
    "thumbnail_url",
    "image_urls",
    "demo_video_url",
    "repo_url",
    "live_url",
    "tech_tags",
    "track",
)


def _require_open_for_edit(event: Event, user):
    """Teams may draft and edit until the deadline; organizers always may."""
    if is_organizer(user, event):
        return
    if not event.submissions_open():
        if event.submissions_closed():
            raise DeadlinePassed(f"Submissions closed at {event.submissions_close_at:%Y-%m-%d %H:%M} UTC.")
        raise DeadlinePassed(f"Submissions open at {event.submissions_open_at:%Y-%m-%d %H:%M} UTC.")


@transaction.atomic
def create_project(event: Event, team: Team, user, data: dict) -> Project:
    _require_open_for_edit(event, user)
    if not is_team_member(user, _proxy(team)) and not is_organizer(user, event):
        raise PermissionDenied("You are not a member of that team.")
    if team.event_id != event.id:
        raise ValidationError("Team belongs to a different event.")
    project = Project(event=event, team=team, created_by=user)
    _apply(project, data)
    project.full_clean(exclude=["external_id"])
    project.save()
    record("project.create", actor=user, event=event, target=project)
    return project


@transaction.atomic
def update_project(project: Project, user, data: dict) -> Project:
    _require_open_for_edit(project.event, user)
    if not is_team_member(user, project) and not is_organizer(user, project.event):
        raise PermissionDenied("You are not a member of this project's team.")
    before = {f: getattr(project, f if f != "track" else "track_id") for f in PROJECT_FIELDS}
    _apply(project, data)
    project.full_clean(exclude=["external_id"])
    project.save()
    after = {f: getattr(project, f if f != "track" else "track_id") for f in PROJECT_FIELDS}
    changed = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
    record("project.update", actor=user, event=project.event, target=project, detail={"changed": changed})
    return project


@transaction.atomic
def submit_project(project: Project, user) -> Project:
    _require_open_for_edit(project.event, user)
    if not is_team_member(user, project) and not is_organizer(user, project.event):
        raise PermissionDenied("You are not a member of this project's team.")
    if not project.title.strip():
        raise ValidationError("A title is required before submitting.")
    unanswered = missing_answers(project)
    if unanswered and not is_organizer(user, project.event):
        raise ValidationError(
            "The organizers ask every team to answer before submitting: " + "; ".join(q.prompt for q in unanswered)
        )
    # Two teammates pressing submit at once must not both get through.
    Team.objects.select_for_update().filter(pk=project.team_id).first()
    already = (
        Project.objects.filter(team=project.team, status=Project.Status.SUBMITTED, duplicate_of__isnull=True)
        .exclude(pk=project.pk)
        .first()
    )
    if already is not None and not is_organizer(user, project.event):
        raise ValidationError(
            f"Your team has already submitted '{already.title}'. Edit that project, or withdraw it before submitting another."
        )
    project.status = Project.Status.SUBMITTED
    project.submitted_at = project.submitted_at or timezone.now()
    project.save(update_fields=["status", "submitted_at", "updated_at"])
    twins = flag_lookalikes(project)
    record("project.submit", actor=user, event=project.event, target=project, detail={"lookalikes": twins})
    return project


def missing_answers(project: Project) -> list:
    """The organizer's required questions this project has not answered."""
    from .models import CustomQuestion

    answered = set(project.answers.exclude(value="").values_list("question_id", flat=True))
    return [q for q in CustomQuestion.objects.filter(event=project.event, required=True) if q.id not in answered]


# Characters that show as nothing: formats and controls, every kind of
# space, and the Hangul fillers, which are letters to Unicode and blanks to
# the eye. An answer made only of these is no answer.
SHOWS_AS_NOTHING = {"\u3164", "\u115f", "\u1160", "\uffa0", "\u2800"}


def _shows(text: str) -> bool:
    import unicodedata

    return any(
        ch not in SHOWS_AS_NOTHING and unicodedata.category(ch) not in ("Cf", "Cc", "Zs", "Zl", "Zp") for ch in text
    )


@transaction.atomic
def set_answers(project: Project, user, answers: dict) -> int:
    """Store answers to the organizer's questions, keyed by question id.
    Each is checked against the kind of its question. The page and the API
    both come through here, so they cannot disagree.

    A draft may leave required questions open. A submitted project may not:
    an edit that would leave one unanswered is refused, unless an organizer
    makes it."""
    from .models import CustomAnswer, CustomQuestion

    _require_open_for_edit(project.event, user)
    if not is_team_member(user, project) and not is_organizer(user, project.event):
        raise PermissionDenied("You are not a member of this project's team.")
    questions = {q.id: q for q in CustomQuestion.objects.filter(event=project.event)}
    saved = 0
    for key, raw in (answers or {}).items():
        q = questions.get(as_id(key) or 0)
        if q is None:
            raise ValidationError(f"There is no question {str(key)[:20]} on this event's form.")
        if isinstance(raw, bool):
            value = "True" if raw else ""
        else:
            value = str(raw if raw is not None else "").strip()
        if "\x00" in value:
            raise ValidationError(f"{q.prompt}: the answer contains a character that cannot be stored.")
        if not _shows(value):
            value = ""
        if len(value) > 5000:
            raise ValidationError(f"{q.prompt}: the answer is longer than 5,000 characters.")
        if value and q.kind == CustomQuestion.Kind.CHOICE and value not in (q.choices or []):
            raise ValidationError(f"{q.prompt}: choose one of {', '.join(q.choices or [])}.")
        if value and q.kind == CustomQuestion.Kind.URL:
            value = _web_address(value, q.prompt)
        if q.kind == CustomQuestion.Kind.CHECKBOX:
            value = "True" if value.lower() in ("true", "1", "yes", "on") else ""
        CustomAnswer.objects.update_or_create(project=project, question=q, defaults={"value": value})
        saved += 1
    if saved and project.status == Project.Status.SUBMITTED and not is_organizer(user, project.event):
        unanswered = missing_answers(project)
        if unanswered:
            raise ValidationError(
                "This project is submitted, and the organizers ask every submitted project to answer: "
                + "; ".join(q.prompt for q in unanswered)
            )
    return saved


def lookalikes(project: Project):
    """Other submitted projects in the event that share this one's repository
    or live address. Shown to organizers; never excluded automatically, because
    two teams forking one starter is not the same as one project entered twice."""
    from django.db.models import Q

    match = Q()
    if project.repo_url:
        match |= Q(repo_url__iexact=project.repo_url)
    if project.live_url:
        match |= Q(live_url__iexact=project.live_url)
    if not match:
        return Project.objects.none()
    return (
        Project.objects.filter(match, event=project.event, status=Project.Status.SUBMITTED)
        .exclude(pk=project.pk)
        .select_related("team")
    )


def flag_lookalikes(project: Project) -> list[int]:
    return list(lookalikes(project).values_list("id", flat=True))


@transaction.atomic
def withdraw_project(project: Project, user) -> Project:
    _require_open_for_edit(project.event, user)
    if not is_team_member(user, project) and not is_organizer(user, project.event):
        raise PermissionDenied("You are not a member of this project's team.")
    project.status = Project.Status.WITHDRAWN
    project.save(update_fields=["status", "updated_at"])
    record("project.withdraw", actor=user, event=project.event, target=project)
    return project


@transaction.atomic
def set_hidden(project: Project, user, hidden: bool) -> Project:
    if not is_organizer(user, project.event):
        raise PermissionDenied("Only organizers can hide projects.")
    project.is_hidden = hidden
    project.save(update_fields=["is_hidden", "updated_at"])
    record("project.hide" if hidden else "project.unhide", actor=user, event=project.event, target=project)
    return project


WEB_ADDRESS = URLValidator(schemes=["http", "https"])
ADDRESS_FIELDS = {
    "thumbnail_url": "Cover image",
    "demo_video_url": "Demo video",
    "repo_url": "Repository",
    "live_url": "Live address",
}


def _web_address(value: str, label: str) -> str:
    """Only http and https. These are printed as links and image sources on
    public pages; anything else (javascript:, data:, ftp:) is refused."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) > 500:
        raise ValidationError(f"{label}: the address is longer than 500 characters.")
    try:
        WEB_ADDRESS(value)
    except ValidationError:
        raise ValidationError(f"{label}: '{value[:60]}' is not an http or https address.") from None
    return value


def _apply(project: Project, data: dict):
    for field in PROJECT_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in ADDRESS_FIELDS:
            value = _web_address(value, ADDRESS_FIELDS[field])
        if field == "image_urls":
            value = [_web_address(v, "Image gallery") for v in _as_list(value)]
            if len(value) > 12:
                raise ValidationError("The image gallery holds at most 12 images.")
        if field == "tech_tags":
            value = _as_list(value)
            if len(value) > 20 or any(len(t) > 40 for t in value):
                raise ValidationError("At most 20 tags, each at most 40 characters.")
        if field == "track" and value is not None:
            value = _track_of(project.event, value)
        setattr(project, field, value)


def _track_of(event: Event, value) -> Track:
    """A track of this event, or a refusal. A track from another event is an
    error the caller should hear about, not something to drop silently."""
    if hasattr(value, "pk"):
        track = value if value.event_id == event.id else None
    else:
        pk = as_id(value)
        track = Track.objects.filter(event=event, pk=pk).first() if pk else None
    if track is None:
        raise ValidationError("That track does not belong to this event.")
    return track


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("\n", ",").split(",")]
    else:
        parts = [str(p).strip() for p in value]
    return [p for p in parts if p]


# --- organizer administration ---------------------------------------------------

EVENT_FIELDS = (
    "name",
    "tagline",
    "description",
    "submissions_open_at",
    "submissions_close_at",
    "judging_open_at",
    "judging_close_at",
    "reviews_per_project",
    "voting_access",
    "voting_open_at",
    "voting_close_at",
    "voting_credits",
    "comments_enabled",
    "is_listed",
    "theme_ground",
    "theme_ink",
    "theme_accent",
    "theme_signal",
)
# Columns that cannot be empty: "null" for these is a mistake, not a value.
EVENT_REQUIRED = (
    "name",
    "submissions_open_at",
    "submissions_close_at",
    "reviews_per_project",
    "voting_access",
    "voting_credits",
    "comments_enabled",
    "is_listed",
    "theme_ground",
    "theme_ink",
    "theme_accent",
    "theme_signal",
)


def clean_slug(raw, name: str = "", exclude_pk=None) -> str:
    """The slug an event will live under. Names that leave nothing behind in
    ASCII (a name written in Hangul, say) get a generated one."""
    slug = (slugify(raw or "") or slugify(name or ""))[:80].strip("-")
    if not slug:
        slug = f"event-{secrets.token_hex(3)}"
    if slug in RESERVED_SLUGS:
        raise ValidationError(f"The slug '{slug}' is used by the portal itself. Choose another.")
    if Event.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        raise ValidationError("That slug is taken.")
    return slug


def _check_event(event: Event):
    for field in EVENT_REQUIRED:
        if getattr(event, field) in (None, ""):
            raise ValidationError(f"{field} cannot be empty.")
    if event.submissions_close_at <= event.submissions_open_at:
        raise ValidationError("submissions_close_at must be after submissions_open_at.")
    judging_starts = event.judging_open_at or event.submissions_close_at
    if event.judging_close_at and event.judging_close_at <= judging_starts:
        raise ValidationError(
            "judging_close_at must be after judging opens"
            + ("." if event.judging_open_at else ", which without judging_open_at is when submissions close.")
        )
    if event.voting_open_at and event.voting_close_at and event.voting_close_at <= event.voting_open_at:
        raise ValidationError("voting_close_at must be after voting_open_at.")
    event.reviews_per_project = as_int(event.reviews_per_project, "reviews_per_project", 1, MAX_REVIEWS_PER_PROJECT)
    event.voting_credits = as_int(event.voting_credits, "voting_credits", 0, 10000)


@transaction.atomic
def create_event(user, data: dict, tracks=()) -> Event:
    """Create an event with its tracks, organizer role and default rubric, or
    nothing at all: a refusal halfway leaves no half-made event behind."""
    if not user or not user.is_authenticated:
        raise PermissionDenied("Sign in to create an event.")
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("An event needs a name.")
    event = Event(name=name, slug=clean_slug(data.get("slug"), name), created_by=user)
    for field in EVENT_FIELDS:
        if field != "name" and data.get(field) is not None:
            setattr(event, field, data[field])
    _check_event(event)
    event.full_clean(exclude=["external_id"])
    event.save()
    _add_tracks(event, tracks)
    EventRole.objects.create(event=event, user=user, role=Role.ORGANIZER)
    from judging.services import ensure_rubric

    ensure_rubric(event)
    record("event.create", actor=user, event=event, target=event)
    return event


def _add_tracks(event: Event, names) -> list[Track]:
    existing = {t.name.lower() for t in event.tracks.all()}
    order = len(existing)
    created = []
    for raw in names or []:
        name = str(raw or "").strip()
        if not name or name.lower() in existing:
            continue
        if len(name) > 120:
            raise ValidationError("A track name can be at most 120 characters.")
        created.append(Track.objects.create(event=event, name=name, order=order))
        existing.add(name.lower())
        order += 1
    return created


@transaction.atomic
def add_tracks(event: Event, user, names) -> list[Track]:
    """Add tracks by name. Names the event already has are skipped."""
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can add tracks.")
    created = _add_tracks(event, names)
    if created:
        record("event.tracks.add", actor=user, event=event, target=event, detail={"tracks": [t.name for t in created]})
    return created


@transaction.atomic
def update_event(event: Event, user, data: dict) -> Event:
    """Change event settings. Every changed field is written to the audit
    log with its old and new value, so a moved deadline is never silent."""
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can change event settings.")
    changed = {}
    if data.get("slug") and data["slug"] != event.slug:
        slug = clean_slug(data["slug"], exclude_pk=event.pk)
        if slug != event.slug:
            changed["slug"] = [event.slug, slug]
            event.slug = slug
    for field in EVENT_FIELDS:
        if field not in data:
            continue
        before = getattr(event, field)
        after = data[field]
        if before != after:
            setattr(event, field, after)
            changed[field] = [_plain(before), _plain(after)]
    _check_event(event)
    event.full_clean(exclude=["external_id"])
    event.save()
    if changed:
        record("event.update", actor=user, event=event, target=event, detail={"changed": changed})
    return event


def _plain(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


@transaction.atomic
def add_judge(event: Event, user, email: str, name: str = "", tracks=None) -> EventRole:
    """Create the judge's account if needed and give them the judge role.
    Passing tracks (a list of Track objects or ids) restricts what they see;
    an empty list means every track."""
    User = get_user_model()

    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can add judges.")
    email = (email or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise ValidationError("A valid email is required.") from None
    if len(email) > 254:
        raise ValidationError("That email address is too long.")
    name = (name or "").strip()
    if len(name) > 150:
        raise ValidationError("A name can be at most 150 characters.")
    track_objs = [_track_of(event, t) for t in tracks or []]
    judge = User.objects.filter(email__iexact=email).order_by("id").first()
    if judge is None:
        username = email.split("@")[0][:140] or "judge"
        base, i = username, 2
        while User.objects.filter(username=username).exists():
            username, i = f"{base}{i}", i + 1
        judge = User.objects.create_user(username=username, email=email)
        judge.set_unusable_password()
        if name:
            first, _, last = name.partition(" ")
            judge.first_name, judge.last_name = first[:150], last[:150]
        judge.save()
    role, created = EventRole.objects.get_or_create(event=event, user=judge, role=Role.JUDGE)
    role.tracks.set(track_objs)
    record(
        "judge.invite" if created else "judge.update",
        actor=user,
        event=event,
        target=role,
        detail={"email": email, "tracks": [t.name for t in track_objs]},
    )
    return role


@transaction.atomic
def remove_judge(role: EventRole, user):
    if not is_organizer(user, role.event):
        raise PermissionDenied("Only organizers can remove judges.")
    from accounts.models import SignInLink
    from judging.models import JudgeAssignment

    if JudgeAssignment.objects.filter(event=role.event, judge=role.user, status="submitted").exists():
        raise ValidationError("This judge has submitted reviews; keep them for the record and reassign instead.")
    JudgeAssignment.objects.filter(event=role.event, judge=role.user).delete()
    # A link this organizer made for the invitation stops working with it.
    SignInLink.objects.filter(user=role.user, used_at__isnull=True, created_by=user).update(expires_at=timezone.now())
    record("judge.remove", actor=user, event=role.event, target=role, detail={"email": role.user.email})
    role.delete()


def can_issue_sign_in_link(actor, target_user) -> bool:
    """Admins may create a sign-in link for anyone. An organizer may create one
    only for an account that (1) has never been used: no password, never
    signed in, no API token, no staff rights; and (2) belongs to nobody else:
    every event it has a role or a team in is one this organizer runs.

    Without the second rule, adding someone else's invited judge to an event
    of your own would be a way to sign in as them."""
    if actor.is_superuser:
        return True
    if target_user.is_staff or target_user.is_superuser or not target_user.is_active:
        return False
    if target_user.last_login is not None or target_user.has_usable_password():
        return False
    if target_user.api_tokens.exists():
        return False
    mine = Event.objects.filter(roles__user=actor, roles__role=Role.ORGANIZER).values("id")
    if EventRole.objects.filter(user=target_user).exclude(event__in=mine).exists():
        return False
    return not TeamMembership.objects.filter(user=target_user).exclude(team__event__in=mine).exists()


@transaction.atomic
def issue_judge_link(role: EventRole, actor) -> str:
    """Create a one-time sign-in link for an invited judge; returns the raw token."""
    from accounts.models import SignInLink

    if not is_organizer(actor, role.event):
        raise PermissionDenied("Only organizers can create sign-in links.")
    if role.role != Role.JUDGE:
        raise ValidationError("Sign-in links are for invited judges.")
    if not can_issue_sign_in_link(actor, role.user):
        raise ValidationError(
            "This judge already has an account of their own, or was invited to an event you do not run. "
            "They sign in with their own password or the link from that event; "
            "an administrator can create a new link for them."
        )
    _, raw = SignInLink.issue(role.user, created_by=actor)
    record("judge.sign_in_link", actor=actor, event=role.event, target=role, detail={"email": role.user.email})
    return raw
