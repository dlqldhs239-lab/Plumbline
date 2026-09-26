"""Role checks. Every view and API endpoint asks these questions instead of
reading roles ad hoc, so isolation is decided in one place, in the backend."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied

from .models import Event, EventRole, Project, Role, TeamMembership


def is_admin(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


def roles_for(user, event: Event) -> set[str]:
    if not user or not user.is_authenticated:
        return set()
    return set(EventRole.objects.filter(event=event, user=user).values_list("role", flat=True))


def is_organizer(user, event: Event) -> bool:
    return is_admin(user) or Role.ORGANIZER in roles_for(user, event)


def is_judge(user, event: Event) -> bool:
    return Role.JUDGE in roles_for(user, event)


def is_participant(user, event: Event) -> bool:
    return Role.PARTICIPANT in roles_for(user, event)


def judge_role(user, event: Event) -> EventRole | None:
    if not user or not user.is_authenticated:
        return None
    return EventRole.objects.filter(event=event, user=user, role=Role.JUDGE).prefetch_related("tracks").first()


def judge_track_ids(user, event: Event) -> set[int] | None:
    """Track ids a judge may see; None means unrestricted (all tracks)."""
    role = judge_role(user, event)
    if role is None:
        return set()
    ids = set(role.tracks.values_list("id", flat=True))
    return ids or None


def is_team_member(user, project: Project) -> bool:
    if not user or not user.is_authenticated:
        return False
    return TeamMembership.objects.filter(team=project.team, user=user).exists()


def can_view_project(user, project: Project) -> bool:
    if project.is_public:
        return True
    return is_team_member(user, project) or is_organizer(user, project.event)


def can_edit_project(user, project: Project) -> bool:
    return is_team_member(user, project) or is_organizer(user, project.event)


def require(condition: bool, message: str = "You do not have access to this."):
    if not condition:
        raise PermissionDenied(message)
