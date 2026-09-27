"""Pairwise judging over the API."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from ninja import Schema

from events.permissions import is_organizer
from judging import pairwise_services
from judging.models import ProjectResult

from .auth import auth_optional, auth_required
from .router import _event, api
from .schemas import ErrorOut


class PairSide(Schema):
    project_id: int
    title: str
    tagline: str = ""
    team: str
    track: str | None = None
    repo_url: str = ""


class NextPairOut(Schema):
    event: str
    done: int
    asked: int
    pair: list[PairSide] | None = None


class ComparisonIn(Schema):
    first: int
    second: int
    preferred: int | None = None


class ComparisonOut(Schema):
    id: int
    left: int
    right: int
    preferred: int | None = None


class PairwiseRow(Schema):
    project_id: int
    title: str
    rank: int | None
    score: float | None
    comparisons: int
    preferred: float
    rubric_rank: int | None


def side(p) -> dict:
    return {
        "project_id": p.id,
        "title": p.title,
        "tagline": p.tagline,
        "team": p.team.name,
        "track": p.track.name if p.track else None,
        "repo_url": p.repo_url,
    }


@api.get("/events/{slug}/pairs/next", response={200: NextPairOut, 403: ErrorOut}, auth=auth_required, tags=["pairwise"])
def next_pair(request, slug: str):
    """The two projects this judge should compare next, or no pair when they
    have done what was asked. Only ever the caller's own assigned projects."""
    event = _event(slug)
    if not pairwise_services.enabled(event):
        raise PermissionDenied("This event does not use pairwise judging.")
    if not event.roles.filter(user=request.user, role="judge").exists():
        raise PermissionDenied("You are not a judge in this event.")
    state = pairwise_services.status(request.user, event)
    pair = pairwise_services.next_pair(request.user, event)
    return {
        "event": event.slug,
        "done": state["done"],
        "asked": state["asked"],
        "pair": [side(p) for p in pair] if pair else None,
    }


@api.post(
    "/events/{slug}/comparisons",
    response={201: ComparisonOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["pairwise"],
)
def compare(request, slug: str, payload: ComparisonIn):
    """Record which of two assigned projects the caller prefers. Leave
    `preferred` out to say neither. One answer per judge and pair."""
    row = pairwise_services.record_comparison(
        request.user, _event(slug), payload.first, payload.second, payload.preferred
    )
    return 201, {"id": row.id, "left": row.left_id, "right": row.right_id, "preferred": row.preferred_id}


@api.get(
    "/events/{slug}/pairwise", response={200: list[PairwiseRow], 403: ErrorOut}, auth=auth_optional, tags=["pairwise"]
)
def pairwise_ranking(request, slug: str):
    """The ranking from comparisons, as of the last recompute. Organizers
    always; everyone else once results are published."""
    event = _event(slug)
    if not event.results_published and not is_organizer(request.user, event):
        raise PermissionDenied("Results are not published yet.")
    from judging.services import eligible_projects

    rows = (
        ProjectResult.objects.filter(event=event, pairwise_n__gt=0, project__in=eligible_projects(event))
        .select_related("project")
        .order_by("pairwise_rank", "id")
    )
    return [
        {
            "project_id": r.project_id,
            "title": r.project.title,
            "rank": r.pairwise_rank,
            "score": r.pairwise_score,
            "comparisons": r.pairwise_n,
            "preferred": r.pairwise_wins,
            "rubric_rank": r.rank,
        }
        for r in rows
    ]
