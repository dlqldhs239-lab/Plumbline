"""Write operations for events, teams and projects.

Views and API endpoints call these; nothing else mutates state. Each function
enforces the rule it is named after and writes an audit entry.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.services import record

from .models import Event, EventRole, Project, Role, Team, TeamInvite, TeamMembership
from .permissions import is_organizer, is_team_member


class DeadlinePassed(PermissionDenied):
    pass


def ensure_participant(user, event: Event) -> EventRole:
    role, _ = EventRole.objects.get_or_create(event=event, user=user, role=Role.PARTICIPANT)
    return role


@transaction.atomic
def create_team(event: Event, user, name: str) -> Team:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Team name is required.")
    if TeamMembership.objects.filter(team__event=event, user=user).exists():
        raise ValidationError("You are already on a team for this event.")
    if Team.objects.filter(event=event, name__iexact=name).exists():
        raise ValidationError("A team with that name already exists in this event.")
    team = Team.objects.create(event=event, name=name, created_by=user)
    TeamMembership.objects.create(team=team, user=user, role=TeamMembership.MemberRole.OWNER)
    ensure_participant(user, event)
    record("team.create", actor=user, event=event, target=team)
    return team


@transaction.atomic
def create_invite(team: Team, user, max_uses: int = 10, ttl_hours: int = 72) -> TeamInvite:
    if not is_team_member(user, _proxy(team)) and not is_organizer(user, team.event):
        raise PermissionDenied("Only team members can create invite links.")
    invite = TeamInvite.objects.create(
        team=team,
        created_by=user,
        max_uses=max_uses,
        expires_at=timezone.now() + timezone.timedelta(hours=ttl_hours),
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
    project.status = Project.Status.SUBMITTED
    project.submitted_at = project.submitted_at or timezone.now()
    project.save(update_fields=["status", "submitted_at", "updated_at"])
    record("project.submit", actor=user, event=project.event, target=project)
    return project


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


def _apply(project: Project, data: dict):
    for field in PROJECT_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field in ("image_urls", "tech_tags"):
            value = _as_list(value)
        if field == "track" and value is not None and not hasattr(value, "pk"):
            from .models import Track

            value = Track.objects.filter(event=project.event, pk=value).first()
        setattr(project, field, value)


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
)


@transaction.atomic
def update_event(event: Event, user, data: dict) -> Event:
    """Change event settings. Every changed field is written to the audit
    log with its old and new value, so a moved deadline is never silent."""
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can change event settings.")
    changed = {}
    for field in EVENT_FIELDS:
        if field not in data:
            continue
        before = getattr(event, field)
        after = data[field]
        if before != after:
            setattr(event, field, after)
            changed[field] = [_plain(before), _plain(after)]
    if event.submissions_close_at <= event.submissions_open_at:
        raise ValidationError("submissions_close_at must be after submissions_open_at.")
    event.full_clean()
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
    from django.contrib.auth.models import User

    from .models import Track

    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can add judges.")
    email = (email or "").strip().lower()
    if "@" not in email:
        raise ValidationError("A valid email is required.")
    judge = User.objects.filter(email__iexact=email).first()
    if judge is None:
        username = email.split("@")[0][:150]
        base, i = username, 2
        while User.objects.filter(username=username).exists():
            username, i = f"{base}{i}", i + 1
        judge = User.objects.create_user(username=username, email=email)
        judge.set_unusable_password()
        if name:
            judge.first_name, _, judge.last_name = name.partition(" ")
        judge.save()
    role, created = EventRole.objects.get_or_create(event=event, user=judge, role=Role.JUDGE)
    track_objs = []
    for t in tracks or []:
        track_objs.append(t if hasattr(t, "pk") else Track.objects.get(pk=t, event=event))
    if any(t.event_id != event.id for t in track_objs):
        raise ValidationError("Tracks must belong to this event.")
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
    from judging.models import JudgeAssignment

    if JudgeAssignment.objects.filter(event=role.event, judge=role.user, status="submitted").exists():
        raise ValidationError("This judge has submitted reviews; keep them for the record and reassign instead.")
    JudgeAssignment.objects.filter(event=role.event, judge=role.user).delete()
    record("judge.remove", actor=user, event=role.event, target=role, detail={"email": role.user.email})
    role.delete()
