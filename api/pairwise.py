"""Pairwise judging over the API."""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import transaction
from ninja import Schema

from events.permissions import is_organizer
from judging import pairwise_services
from judging.services import computed_jury_k, ensure_rubric, set_jury_k

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


class MethodIn(Schema):
    pairwise: bool | None = None
    jury_size: int | None = None
    jury_size_follows_event: bool = False


class MethodOut(Schema):
    pairwise: bool
    jury_size: int | None
    jury_size_in_use: float | None


def method_out(event) -> dict:
    rubric = ensure_rubric(event)
    return {"pairwise": rubric.pairwise, "jury_size": rubric.jury_k, "jury_size_in_use": computed_jury_k(event)}


@api.get(
    "/events/{slug}/judging/method", response={200: MethodOut, 403: ErrorOut}, auth=auth_required, tags=["judging"]
)
def get_method(request, slug: str):
    """How this event is judged beyond the rubric: whether judges are also
    asked to compare pairs, and the jury-size constant. Organizers only."""
    event = _event(slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    return method_out(event)


@api.patch(
    "/events/{slug}/judging/method",
    response={200: MethodOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def set_method(request, slug: str, payload: MethodIn):
    """Change only what is sent. `jury_size` 0 switches the adjustment off;
    `jury_size_follows_event` puts it back to the event's reviews per project.
    Results change at the next recompute, not before."""
    event = _event(slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    with transaction.atomic():
        if payload.jury_size_follows_event:
            set_jury_k(ensure_rubric(event), request.user, None)
        elif payload.jury_size is not None:
            set_jury_k(ensure_rubric(event), request.user, payload.jury_size)
        if payload.pairwise is not None:
            pairwise_services.set_enabled(event, request.user, payload.pairwise)
    return method_out(event)


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
    always; everyone else once results are published. Places are counted
    among the projects shown, as on the results page. Empty when the event
    does not use pairwise judging."""
    event = _event(slug)
    if not event.results_published and not is_organizer(request.user, event):
        raise PermissionDenied("Results are not published yet.")
    if not pairwise_services.enabled(event):
        return []
    return [
        {
            "project_id": r.project_id,
            "title": r.project.title,
            "rank": r.pairwise_place,
            "score": r.pairwise_score,
            "comparisons": r.pairwise_n,
            "preferred": r.pairwise_wins,
            "rubric_rank": r.rubric_place,
        }
        for r in pairwise_services.ranking(event)
    ]
