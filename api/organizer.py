"""Organizer administration endpoints.

API first: everything the organizer console can do is here too, behind the
same service functions, so the role checks and audit entries are identical
whichever way an action arrives.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from community import services as community_services
from community.models import Comment, Voter
from events import services as event_services
from events.models import EventRole, Project, Role
from judging import services as judging_services
from judging.models import JudgeAssignment

from .auth import auth_optional, auth_required
from .organizer_schemas import (
    AuditOut,
    BallotLinkOut,
    BallotLinksIn,
    CriterionIn,
    EventPatch,
    JudgeIn,
    JudgeOut,
    ManualAssignIn,
    RubricOut,
    VoterOut,
)
from .router import _event, _require_organizer, _resolve_judge, api, assignment_out, event_out, project_out
from .schemas import AssignmentOut, CommentOut, ErrorOut, EventOut, ProjectOut


@api.patch(
    "/events/{slug}", response={200: EventOut, 400: ErrorOut, 403: ErrorOut}, auth=auth_required, tags=["events"]
)
def update_event(request, slug: str, payload: EventPatch):
    """Change event settings. Each changed field is audited with its old and new value."""
    event = _event(slug)
    event_services.update_event(event, request.user, payload.dict(exclude_unset=True))
    return event_out(event)


# --- judges ------------------------------------------------------------------


def judge_out(role: EventRole) -> dict:
    return {
        "id": role.id,
        "external_id": role.external_id,
        "username": role.user.username,
        "email": role.user.email,
        "name": role.user.get_full_name(),
        "tracks": [t.name for t in role.tracks.all()],
    }


@api.get("/events/{slug}/judges", response=list[JudgeOut], auth=auth_required, tags=["judging"])
def list_judges(request, slug: str):
    event = _event(slug)
    _require_organizer(request, event)
    roles = EventRole.objects.filter(event=event, role=Role.JUDGE).select_related("user").prefetch_related("tracks")
    return [judge_out(r) for r in roles]


@api.post(
    "/events/{slug}/judges",
    response={201: JudgeOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def add_judge(request, slug: str, payload: JudgeIn):
    """Add a judge by email, or update their tracks. Creates the account if needed."""
    event = _event(slug)
    role = event_services.add_judge(event, request.user, payload.email, payload.name, payload.tracks)
    return 201, judge_out(role)


@api.delete(
    "/events/{slug}/judges/{judge_ref}",
    response={204: None, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def remove_judge(request, slug: str, judge_ref: str):
    """Refused if the judge has submitted reviews; those are part of the record."""
    event = _event(slug)
    _require_organizer(request, event)
    judge = _resolve_judge(judge_ref)
    role = get_object_or_404(EventRole, event=event, user=judge, role=Role.JUDGE)
    event_services.remove_judge(role, request.user)
    return 204, None


# --- rubric ------------------------------------------------------------------


def rubric_out(event) -> dict:
    rubric = judging_services.ensure_rubric(event)
    return {
        "name": rubric.name,
        "scale_min": rubric.scale_min,
        "scale_max": rubric.scale_max,
        "instructions": rubric.instructions,
        "criteria": [
            {"key": c.key, "name": c.name, "weight": float(c.weight), "description": c.description}
            for c in rubric.criteria.all()
        ],
    }


@api.get("/events/{slug}/rubric", response=RubricOut, auth=auth_optional, tags=["judging"])
def get_rubric(request, slug: str):
    """The rubric is public: participants should know how they are judged."""
    return rubric_out(_event(slug))


@api.put(
    "/events/{slug}/rubric",
    response={200: RubricOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def replace_rubric(request, slug: str, payload: list[CriterionIn]):
    """Replace the criteria set. Allowed until the first score exists."""
    event = _event(slug)
    rubric = judging_services.ensure_rubric(event)
    judging_services.replace_criteria(rubric, request.user, [c.dict() for c in payload])
    return rubric_out(event)


# --- projects and assignments ------------------------------------------------


@api.post(
    "/events/{slug}/projects/{project_id}/hide",
    response={200: ProjectOut, 403: ErrorOut},
    auth=auth_required,
    tags=["projects"],
)
def hide_project(request, slug: str, project_id: int, hidden: bool = True):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track", "event"), pk=project_id, event=event)
    return project_out(event_services.set_hidden(project, request.user, hidden))


@api.post(
    "/events/{slug}/assignments",
    response={201: AssignmentOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def manual_assign(request, slug: str, payload: ManualAssignIn):
    event = _event(slug)
    _require_organizer(request, event)
    judge = _resolve_judge(payload.judge)
    project = get_object_or_404(Project, pk=payload.project_id, event=event)
    created = judging_services.assign_manual(event, request.user, judge, project, payload.batch)
    assignment = (
        JudgeAssignment.objects.select_related("project", "project__track", "event")
        .prefetch_related("scores__criterion")
        .get(pk=created.pk)
    )
    return 201, assignment_out(assignment)


@api.delete(
    "/events/{slug}/assignments/{assignment_id}",
    response={204: None, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def remove_assignment(request, slug: str, assignment_id: int):
    event = _event(slug)
    assignment = get_object_or_404(JudgeAssignment, pk=assignment_id, event=event)
    judging_services.unassign(assignment, request.user)
    return 204, None


# --- voters and comments -----------------------------------------------------


def voter_out(voter: Voter) -> dict:
    return {
        "id": voter.id,
        "kind": voter.kind,
        "label": str(voter),
        "votes": voter.votes.count(),
        "flags": voter.flags,
        "voided": voter.voided_at is not None,
    }


@api.get("/events/{slug}/voters", response=list[VoterOut], auth=auth_required, tags=["community"])
def list_voters(request, slug: str):
    event = _event(slug)
    _require_organizer(request, event)
    return [voter_out(v) for v in Voter.objects.filter(event=event).order_by("-created_at")]


@api.post(
    "/events/{slug}/voters/links",
    response={200: list[BallotLinkOut], 403: ErrorOut},
    auth=auth_required,
    tags=["community"],
)
def issue_ballot_links(request, slug: str, payload: BallotLinksIn):
    """Email mode: one ballot link per address, returned for the organizer to send."""
    event = _event(slug)
    created = community_services.create_email_voters(event, request.user, payload.emails)
    return [
        {"email": v.email, "url": request.build_absolute_uri(f"/events/{event.slug}/ballot/{v.ballot_token}/")}
        for v in created
    ]


@api.post(
    "/events/{slug}/voters/{voter_id}/void",
    response={200: VoterOut, 403: ErrorOut},
    auth=auth_required,
    tags=["community"],
)
def void_voter(request, slug: str, voter_id: int, reason: str = "organizer review"):
    """Exclude a ballot from the tally. Its votes are kept, not deleted."""
    event = _event(slug)
    voter = get_object_or_404(Voter, pk=voter_id, event=event)
    return voter_out(community_services.void_voter(voter, request.user, reason))


@api.post(
    "/events/{slug}/projects/{project_id}/comments/{comment_id}/hide",
    response={200: CommentOut, 403: ErrorOut},
    auth=auth_required,
    tags=["community"],
)
def hide_comment(request, slug: str, project_id: int, comment_id: int, hidden: bool = True):
    event = _event(slug)
    comment = get_object_or_404(Comment, pk=comment_id, project_id=project_id, project__event=event)
    comment = community_services.hide_comment(comment, request.user, hidden)
    return {
        "id": comment.id,
        "author": comment.author.username,
        "body": comment.body,
        "created_at": comment.created_at,
        "hidden": comment.is_hidden,
    }


# --- audit -------------------------------------------------------------------


@api.get("/events/{slug}/audit", response=list[AuditOut], auth=auth_required, tags=["export"])
def audit_log(request, slug: str, action: str = "", limit: int = 200, before_id: int | None = None):
    """The audit log as JSON, newest first. Page backwards with before_id."""
    event = _event(slug)
    _require_organizer(request, event)
    qs = event.audit_entries.all()
    if action:
        qs = qs.filter(action__startswith=action)
    if before_id:
        qs = qs.filter(id__lt=before_id)
    return [
        {
            "id": e.id,
            "at": e.created_at,
            "actor": e.actor_label,
            "action": e.action,
            "target_type": e.target_type,
            "target_id": e.target_id,
            "target": e.target_label,
            "channel": e.channel,
            "detail": e.detail,
        }
        for e in qs[: max(1, min(limit, 1000))]
    ]
