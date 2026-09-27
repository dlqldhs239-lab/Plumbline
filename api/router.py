"""Plumbline REST API.

Every action the UI can take is available here. Role isolation is enforced in
these functions and in the service layer, never in a template: a judge asking
for another judge's scores gets 403 whether they use the UI, curl or the
checker.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DataError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Query
from ninja.errors import HttpError

from audit.services import record
from events import services as event_services
from events.models import Event, EventRole, Project, Role, Team, TeamMembership
from events.permissions import can_view_project, is_admin, is_organizer, roles_for
from judging import export as export_services
from judging import services as judging_services
from judging.models import JudgeAssignment
from plumbline.inputs import as_id, site_url

from .auth import auth_optional, auth_required
from .schemas import (
    AssignmentOut,
    AssignRequest,
    BallotItem,
    BallotOut,
    CommentIn,
    CommentOut,
    ErrorOut,
    EventIn,
    EventOut,
    JudgeScoresOut,
    ProgressRow,
    ProjectIn,
    ProjectOut,
    ResultOut,
    ScoreValues,
    TeamOut,
    VoteIn,
)

api = NinjaAPI(
    title="Plumbline API",
    version="1.0.0",
    description="Self-hostable hackathon submission and judging portal. Bearer tokens are issued at /accounts/tokens/.",
    docs_url="/docs",
)
# CSRF for browser-session calls is enforced inside api.auth; bearer tokens need none.


@api.exception_handler(PermissionDenied)
def _forbidden(request, exc):
    return api.create_response(request, {"detail": str(exc) or "Forbidden"}, status=403)


@api.exception_handler(ValidationError)
def _bad_request(request, exc):
    detail = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    return api.create_response(request, {"detail": detail}, status=400)


@api.exception_handler(OverflowError)
@api.exception_handler(DataError)
def _out_of_range(request, exc):
    """A number the database cannot hold: a wrong address when reading, a
    refused value when writing. Never a crash."""
    if request.method in ("GET", "HEAD"):
        return api.create_response(request, {"detail": "Not found"}, status=404)
    return api.create_response(request, {"detail": "A value is out of range or too long."}, status=400)


# --- serializers -------------------------------------------------------------


def event_out(event: Event) -> dict:
    return {
        "id": event.id,
        "slug": event.slug,
        "name": event.name,
        "tagline": event.tagline,
        "submissions_open_at": event.submissions_open_at,
        "submissions_close_at": event.submissions_close_at,
        "judging_open_at": event.judging_open_at,
        "judging_close_at": event.judging_close_at,
        "results_published_at": event.results_published_at,
        "phase": event.phase(),
        "tracks": [{"id": t.id, "name": t.name, "external_id": t.external_id} for t in event.tracks.all()],
    }


def project_out(p: Project) -> dict:
    return {
        "id": p.id,
        "event": p.event.slug,
        "team": p.team.name,
        "track": p.track.name if p.track else None,
        "title": p.title,
        "tagline": p.tagline,
        "description": p.description,
        "thumbnail_url": p.thumbnail_url,
        "image_urls": p.image_urls or [],
        "demo_video_url": p.demo_video_url,
        "repo_url": p.repo_url,
        "live_url": p.live_url,
        "tech_tags": p.tech_tags or [],
        "status": p.status,
        "submitted_at": p.submitted_at,
    }


def assignment_out(a: JudgeAssignment) -> dict:
    return {
        "id": a.id,
        "event": a.event.slug,
        "project_id": a.project_id,
        "project_title": a.project.title,
        "track": a.project.track.name if a.project.track else None,
        "batch": a.batch,
        "status": a.status,
        "scores": {s.criterion.key: s.value for s in a.scores.all()},
        "comment": a.comment,
        "submitted_at": a.submitted_at,
    }


def _event(slug: str) -> Event:
    return get_object_or_404(Event.objects.prefetch_related("tracks"), slug=slug)


def _require_organizer(request, event: Event):
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required for this event.")


# --- events ------------------------------------------------------------------


@api.get("/events", response=list[EventOut], auth=auth_optional, tags=["events"])
def list_events(request):
    qs = Event.objects.prefetch_related("tracks")
    if not is_admin(request.user):
        visible = Q(is_listed=True)
        if request.user.is_authenticated:
            visible |= Q(roles__user=request.user)
        qs = qs.filter(visible).distinct()
    return [event_out(e) for e in qs]


@api.post("/events", response={201: EventOut, 400: ErrorOut}, auth=auth_required, tags=["events"])
def create_event(request, payload: EventIn):
    """Create an event and become its organizer. Refused as a whole if any
    part is wrong: no event is left behind without its tracks or its organizer."""
    event = event_services.create_event(request.user, payload.dict(exclude={"tracks"}), payload.tracks)
    return 201, event_out(event)


@api.get("/events/{slug}", response=EventOut, auth=auth_optional, tags=["events"])
def get_event(request, slug: str):
    return event_out(_event(slug))


# --- teams -------------------------------------------------------------------


@api.get("/events/{slug}/teams/mine", response={200: TeamOut, 404: ErrorOut}, auth=auth_required, tags=["teams"])
def my_team(request, slug: str):
    event = _event(slug)
    membership = TeamMembership.objects.filter(team__event=event, user=request.user).select_related("team").first()
    if membership is None:
        return 404, {"detail": "You are not on a team in this event."}
    team = membership.team
    return {"id": team.id, "name": team.name, "members": [u.username for u in team.member_users()]}


@api.post("/events/{slug}/teams", response={201: TeamOut}, auth=auth_required, tags=["teams"])
def create_team(request, slug: str, name: str):
    event = _event(slug)
    team = event_services.create_team(event, request.user, name)
    return 201, {"id": team.id, "name": team.name, "members": [u.username for u in team.member_users()]}


@api.post("/events/{slug}/teams/{team_id}/invites", auth=auth_required, tags=["teams"])
def create_invite(request, slug: str, team_id: int, max_uses: int = 10, ttl_hours: int = 72):
    event = _event(slug)
    team = get_object_or_404(Team, pk=team_id, event=event)
    invite = event_services.create_invite(team, request.user, max_uses=max_uses, ttl_hours=ttl_hours)
    return {
        "token": invite.token,
        "url": site_url(request, invite.get_absolute_url()),
        "expires_at": invite.expires_at,
    }


@api.post("/teams/join/{token}", response=TeamOut, auth=auth_required, tags=["teams"])
def join_team(request, token: str):
    from events.models import TeamInvite

    invite = get_object_or_404(TeamInvite, token=token)
    membership = event_services.join_team(invite, request.user)
    team = membership.team
    return {"id": team.id, "name": team.name, "members": [u.username for u in team.member_users()]}


# --- projects ----------------------------------------------------------------


@api.get("/events/{slug}/projects", response=list[ProjectOut], auth=auth_optional, tags=["projects"])
def list_projects(request, slug: str, q: str = "", track: int | None = None, mine: bool = False):
    """The public gallery as JSON. `mine=true` returns the caller's own
    projects (including drafts); organizers see everything."""
    event = _event(slug)
    qs = Project.objects.filter(event=event).select_related("team", "track", "event")
    if mine and request.user.is_authenticated:
        qs = qs.filter(team__memberships__user=request.user)
    elif not is_organizer(request.user, event):
        qs = qs.filter(status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True)
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(tagline__icontains=q) | Q(team__name__icontains=q) | Q(tech_tags__icontains=q)
        )
    if track is not None:
        track_id = as_id(track)
        qs = qs.filter(track_id=track_id) if track_id else qs.none()
    return [project_out(p) for p in qs.distinct()]


@api.post(
    "/events/{slug}/projects",
    response={201: ProjectOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["projects"],
)
def create_project(request, slug: str, payload: ProjectIn):
    """Create a draft (or submit directly with submit=true). Refused with 403
    once the event's submission deadline has passed."""
    event = _event(slug)
    if not event.submissions_open() and not is_organizer(request.user, event):
        raise event_services.DeadlinePassed(
            f"Submissions for {event.name} closed at {event.submissions_close_at:%Y-%m-%d %H:%M} UTC."
            if event.submissions_closed()
            else f"Submissions for {event.name} open at {event.submissions_open_at:%Y-%m-%d %H:%M} UTC."
        )
    team = _resolve_team(request, event, payload.team_id)
    data = payload.dict(exclude={"team_id", "track_id", "submit", "answers"})
    data["track"] = payload.track_id
    # One unit: if submit=true is refused, no draft is left behind either.
    with transaction.atomic():
        project = event_services.create_project(event, team, request.user, data)
        if payload.answers:
            event_services.set_answers(project, request.user, payload.answers)
        if payload.submit:
            event_services.submit_project(project, request.user)
    return 201, project_out(project)


def _resolve_team(request, event: Event, team_id: int | None) -> Team:
    if team_id is not None:
        return get_object_or_404(Team, pk=as_id(team_id) or 0, event=event)
    membership = TeamMembership.objects.filter(team__event=event, user=request.user).select_related("team").first()
    if membership is None:
        raise ValidationError("Create or join a team in this event first (or pass team_id).")
    return membership.team


@api.get("/events/{slug}/projects/{project_id}", response=ProjectOut, auth=auth_optional, tags=["projects"])
def get_project(request, slug: str, project_id: int):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track", "event"), pk=project_id, event=event)
    if not can_view_project(request.user, project):
        raise PermissionDenied("This project is not public.")
    return project_out(project)


@api.patch("/events/{slug}/projects/{project_id}", response=ProjectOut, auth=auth_required, tags=["projects"])
def update_project(request, slug: str, project_id: int, payload: ProjectIn):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track", "event"), pk=project_id, event=event)
    data = payload.dict(exclude_unset=True, exclude={"team_id", "track_id", "submit", "answers"})
    if "track_id" in payload.dict(exclude_unset=True):
        data["track"] = payload.track_id
    with transaction.atomic():
        project = event_services.update_project(project, request.user, data)
        if payload.answers:
            event_services.set_answers(project, request.user, payload.answers)
        if payload.submit:
            event_services.submit_project(project, request.user)
    return project_out(project)


@api.post("/events/{slug}/projects/{project_id}/submit", response=ProjectOut, auth=auth_required, tags=["projects"])
def submit_project(request, slug: str, project_id: int):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track", "event"), pk=project_id, event=event)
    return project_out(event_services.submit_project(project, request.user))


@api.post("/events/{slug}/projects/{project_id}/withdraw", response=ProjectOut, auth=auth_required, tags=["projects"])
def withdraw_project(request, slug: str, project_id: int):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track", "event"), pk=project_id, event=event)
    return project_out(event_services.withdraw_project(project, request.user))


# --- judging -----------------------------------------------------------------


def _judge_scores_payload(judge: User, event: Event | None) -> dict:
    qs = (
        judging_services.assignments_for_judge(judge, event)
        .prefetch_related("scores__criterion")
        .order_by("event_id", "id")
    )
    return {
        "judge": judge.username,
        "event": event.slug if event else None,
        "assignments": [assignment_out(a) for a in qs],
    }


@api.get("/judges/me/scores", response={200: JudgeScoresOut, 403: ErrorOut}, auth=auth_required, tags=["judging"])
def my_scores(request, event: str | None = Query(None, description="Event slug")):
    """The caller's own assignments and scores. Only judges have any."""
    ev = _event(event) if event else None
    if not EventRole.objects.filter(user=request.user, role=Role.JUDGE, **({"event": ev} if ev else {})).exists():
        raise PermissionDenied("You are not a judge" + (f" in {ev.name}." if ev else "."))
    return _judge_scores_payload(request.user, ev)


@api.get(
    "/judges/{judge_ref}/scores",
    response={200: JudgeScoresOut, 403: ErrorOut, 404: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def judge_scores(request, judge_ref: str, event: str | None = Query(None, description="Event slug")):
    """A specific judge's scores. Allowed only for that judge themself, or for
    an organizer/admin of the event in question. Everyone else gets 403 here,
    in the backend, before any score is read."""
    ev = _event(event) if event else None
    try:
        judge = _resolve_judge(judge_ref)
    except Http404:
        # Only someone who may read judges' scores is told that a name does
        # not exist. For everyone else the answer is the same either way.
        allowed = is_organizer(request.user, ev) if ev is not None else _organizes_something(request.user)
        if not allowed:
            raise PermissionDenied("Another judge's scores are not visible to you.") from None
        raise
    if judge.id == request.user.id:
        return my_scores(request, event)
    if ev is not None:
        _require_organizer(request, ev)
        return _judge_scores_payload(judge, ev)
    if is_admin(request.user):
        return _judge_scores_payload(judge, None)
    organized = Event.objects.filter(roles__user=request.user, roles__role=Role.ORGANIZER)
    if not organized.exists():
        raise PermissionDenied("Another judge's scores are not visible to you.")
    qs = judging_services.assignments_for_judge(judge).filter(event__in=organized).prefetch_related("scores__criterion")
    return {"judge": judge.username, "event": None, "assignments": [assignment_out(a) for a in qs]}


def _organizes_something(user) -> bool:
    return is_admin(user) or EventRole.objects.filter(user=user, role=Role.ORGANIZER).exists()


def _resolve_judge(ref: str) -> User:
    ref = (ref or "").strip()
    if not ref or len(ref) > 150:
        raise Http404("No such judge.")
    role = EventRole.objects.filter(role=Role.JUDGE, external_id=ref).select_related("user").first()
    if role:
        return role.user
    if as_id(ref):
        by_id = User.objects.filter(pk=as_id(ref)).first()
        if by_id is not None:
            return by_id
    return get_object_or_404(User, username=ref)


@api.get("/judges/me/assignments/{assignment_id}", response=AssignmentOut, auth=auth_required, tags=["judging"])
def my_assignment(request, assignment_id: int):
    a = judging_services.get_own_assignment(request.user, assignment_id)
    return assignment_out(a)


@api.post(
    "/judges/me/assignments/{assignment_id}/scores",
    response={200: AssignmentOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["judging"],
)
def score_assignment(request, assignment_id: int, payload: ScoreValues):
    a = judging_services.get_own_assignment(request.user, assignment_id)
    a = judging_services.save_scores(a, request.user, payload.scores, payload.comment, submit=payload.submit)
    return assignment_out(a)


@api.get("/events/{slug}/assignments", response=list[AssignmentOut], auth=auth_required, tags=["judging"])
def list_assignments(request, slug: str, batch: str | None = None):
    """Organizers see every assignment; a judge sees only their own."""
    event = _event(slug)
    if is_organizer(request.user, event):
        qs = JudgeAssignment.objects.filter(event=event)
    elif Role.JUDGE in roles_for(request.user, event):
        qs = judging_services.assignments_for_judge(request.user, event)
    else:
        raise PermissionDenied("Judge or organizer role required.")
    if batch:
        qs = qs.filter(batch=batch)
    return [
        assignment_out(a)
        for a in qs.select_related("project", "project__track", "event").prefetch_related("scores__criterion")
    ]


@api.post("/events/{slug}/assignments/auto", auth=auth_required, tags=["judging"])
def auto_assign(request, slug: str, payload: AssignRequest):
    event = _event(slug)
    return judging_services.assign_balanced(
        event, request.user, payload.reviews_per_project, payload.batch, payload.seed
    )


@api.get("/events/{slug}/progress", response=list[ProgressRow], auth=auth_required, tags=["judging"])
def judging_progress(request, slug: str):
    event = _event(slug)
    _require_organizer(request, event)
    return judging_services.progress(event)


# --- results -----------------------------------------------------------------


@api.post("/events/{slug}/results/recompute", auth=auth_required, tags=["results"])
def recompute(request, slug: str):
    event = _event(slug)
    return judging_services.recompute_results(event, request.user)


@api.post("/events/{slug}/results/publish", auth=auth_required, tags=["results"])
def publish(request, slug: str, publish: bool = True):
    event = _event(slug)
    judging_services.publish_results(event, request.user, publish)
    return {"published": event.results_published}


@api.get("/events/{slug}/results", response={200: list[ResultOut], 403: ErrorOut}, auth=auth_optional, tags=["results"])
def results(request, slug: str):
    """Standings. Hidden from everyone but organizers until published."""
    event = _event(slug)
    if not event.results_published and not is_organizer(request.user, event):
        raise PermissionDenied("Results are not published yet.")
    out = []
    scale_max = judging_services.ensure_rubric(event).scale_max
    for r in judging_services.standings(event):
        out.append(
            {
                "project_id": r.project_id,
                "title": r.project.title,
                "track": r.project.track.name if r.project.track else None,
                "team": r.project.team.name,
                "review_count": r.review_count,
                "raw_mean": r.raw_mean,
                "normalized_mean": r.normalized_mean,
                "rank_raw": r.rank_raw,
                "rank_normalized": r.rank_normalized,
                "adjusted_mean": r.adjusted_mean,
                "rank": r.rank,
                "scale_max": scale_max,
                "criterion_means": r.criterion_means,
                "community_score": r.community_score,
                "method": r.method,
            }
        )
    return out


# --- community voting and comments ------------------------------------------
# API voting is for signed-in voters (bearer token = an account), so it is
# available in the "auth" mode and, for account holders, the "open" mode.


@api.get("/events/{slug}/ballot", response={200: BallotOut, 403: ErrorOut}, auth=auth_required, tags=["community"])
def ballot(request, slug: str):
    """The caller's ballot: eligible projects in their personal random order
    and their current weights. Totals are never returned here."""
    from community import services as community_services

    event = _event(slug)
    voter = community_services.admit_voter(request, event)
    weights = community_services.voter_weights(voter)
    items = [
        {
            "project_id": p.id,
            "title": p.title,
            "tagline": p.tagline,
            "track": p.track.name if p.track else None,
            "team": p.team.name,
            "my_weight": weights.get(p.id, 0),
        }
        for p in community_services.ballot_projects(event, voter.key)
    ]
    used = sum(w * w for w in weights.values())
    return {
        "event": event.slug,
        "quadratic": bool(event.voting_credits),
        "credits": event.voting_credits,
        "credits_used": used,
        "items": items,
    }


@api.post(
    "/events/{slug}/projects/{project_id}/vote",
    response={200: BallotItem, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["community"],
)
def vote(request, slug: str, project_id: int, payload: VoteIn):
    """Set the caller's weight on a project (0 removes the vote)."""
    from community import services as community_services

    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track"), pk=project_id, event=event)
    voter = community_services.admit_voter(request, event)
    community_services.cast_vote(request, event, voter, project, payload.weight)
    return {
        "project_id": project.id,
        "title": project.title,
        "tagline": project.tagline,
        "track": project.track.name if project.track else None,
        "team": project.team.name,
        "my_weight": payload.weight,
    }


@api.get(
    "/events/{slug}/projects/{project_id}/comments", response=list[CommentOut], auth=auth_optional, tags=["community"]
)
def list_comments(request, slug: str, project_id: int):
    event = _event(slug)
    project = get_object_or_404(Project, pk=project_id, event=event)
    if not can_view_project(request.user, project):
        raise PermissionDenied("This project is not public.")
    qs = project.comments.select_related("author")
    if not is_organizer(request.user, event):
        qs = qs.filter(hidden_at__isnull=True)
    return [
        {"id": c.id, "author": c.author.username, "body": c.body, "created_at": c.created_at, "hidden": c.is_hidden}
        for c in qs
    ]


@api.post(
    "/events/{slug}/projects/{project_id}/comments",
    response={201: CommentOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["community"],
)
def add_comment(request, slug: str, project_id: int, payload: CommentIn):
    from community import services as community_services

    event = _event(slug)
    project = get_object_or_404(Project, pk=project_id, event=event)
    c = community_services.add_comment(request, project, payload.body)
    return 201, {"id": c.id, "author": c.author.username, "body": c.body, "created_at": c.created_at, "hidden": False}


@api.get("/events/{slug}/votes/summary", auth=auth_required, tags=["community"])
def votes_summary(request, slug: str):
    """Organizer view of the tally and integrity flags. Hidden from everyone
    else while voting is open, and after it too unless results are published."""
    from community import services as community_services

    event = _event(slug)
    _require_organizer(request, event)
    report = community_services.integrity_report(event)
    return {
        "tally": {str(k): v for k, v in community_services.tally(event).items()},
        "ballots": report["total"],
        "voided": report["voided"],
        "flagged": report["flagged"],
        "by_kind": report["by_kind"],
        "shared_ips": report["shared_ips"],
    }


# --- export ------------------------------------------------------------------


@api.get("/events/{slug}/export/{kind}.csv", auth=auth_required, tags=["export"])
def export_csv(request, slug: str, kind: str):
    """CSV at every stage: projects, assignments, scores, results, calibration, audit."""
    event = _event(slug)
    _require_organizer(request, event)
    builder = export_services.EXPORTS.get(kind)
    if builder is None:
        raise HttpError(404, f"Unknown export '{kind}'. Choose one of: {', '.join(export_services.EXPORTS)}")
    filename, rows = builder(event)
    record("export.csv", actor=request.user, event=event, detail={"kind": kind, "rows": len(rows) - 1})
    response = HttpResponse(export_services.to_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
