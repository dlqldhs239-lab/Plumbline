"""Judging operations: rubric setup, assignment, scoring, normalization runs,
result publication. All role checks happen here or in events.permissions."""

from __future__ import annotations

import random
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit.services import record
from events.models import Event, EventRole, Project, Role, TeamMembership
from events.permissions import is_organizer, judge_track_ids

from .models import Criterion, JudgeAssignment, JudgeCalibration, ProjectResult, Rubric, Score
from .normalization import Review, normalize, weighted_score

# --- rubric ---------------------------------------------------------------


def ensure_rubric(event: Event) -> Rubric:
    rubric, created = Rubric.objects.get_or_create(event=event)
    if created and not rubric.criteria.exists():
        for i, (key, name) in enumerate(
            [("functionality", "Functionality"), ("quality", "Quality"), ("innovation", "Innovation")]
        ):
            Criterion.objects.create(rubric=rubric, key=key, name=name, order=i)
    return rubric


@transaction.atomic
def replace_criteria(rubric: Rubric, user, rows: list[dict]):
    """Replace the criteria set. Existing scores for removed criteria are
    deleted with them, so this is only allowed before scoring starts."""
    if not is_organizer(user, rubric.event):
        raise PermissionDenied("Only organizers can edit the rubric.")
    if Score.objects.filter(criterion__rubric=rubric).exists():
        raise ValidationError("Scores exist for this rubric; criteria can no longer be replaced.")
    rubric.criteria.all().delete()
    for i, row in enumerate(rows):
        Criterion.objects.create(
            rubric=rubric,
            key=row["key"],
            name=row["name"],
            description=row.get("description", ""),
            weight=Decimal(str(row.get("weight", 1))),
            order=i,
        )
    record("rubric.replace", actor=user, event=rubric.event, target=rubric, detail={"criteria": rows})


# --- assignment ------------------------------------------------------------


def eligible_projects(event: Event):
    return Project.objects.filter(
        event=event, status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True
    )


def judges_for(event: Event):
    return EventRole.objects.filter(event=event, role=Role.JUDGE).select_related("user").prefetch_related("tracks")


def conflicted(judge_user, project: Project) -> bool:
    return TeamMembership.objects.filter(team=project.team, user=judge_user).exists()


@transaction.atomic
def assign_balanced(
    event: Event, user, reviews_per_project: int | None = None, batch: str | None = None, seed: int | None = None
) -> dict:
    """Spread projects over judges so every project gets k reviews, judges get
    an even load, track restrictions are respected and nobody reviews their
    own team. Existing assignments are kept and counted toward k.

    Greedy: walk projects from fewest-existing-reviews to most, give each to
    the eligible judge with the lightest load; ties broken randomly (seeded so
    an organizer can reproduce a run). Judges never see each other's batch:
    the batch label is just for organizer bookkeeping.
    """
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can assign judges.")
    k = reviews_per_project or event.reviews_per_project
    batch = batch or timezone.now().strftime("batch-%Y%m%d-%H%M")
    rng = random.Random(seed)

    judge_roles = list(judges_for(event))
    if not judge_roles:
        raise ValidationError("This event has no judges yet.")
    load = {r.user_id: 0 for r in judge_roles}
    for row in JudgeAssignment.objects.filter(event=event).values("judge_id").annotate(n=Count("id")):
        load[row["judge_id"]] = row["n"]
    tracks_of = {r.user_id: set(r.tracks.values_list("id", flat=True)) for r in judge_roles}
    users_of = {r.user_id: r.user for r in judge_roles}

    existing = defaultdict(set)
    for a in JudgeAssignment.objects.filter(event=event).values_list("project_id", "judge_id"):
        existing[a[0]].add(a[1])

    projects = list(eligible_projects(event).select_related("team"))
    projects.sort(key=lambda p: (len(existing[p.id]), p.id))
    created = 0
    short = []
    for project in projects:
        needed = k - len(existing[project.id])
        for _ in range(max(0, needed)):
            candidates = [
                jid
                for jid in load
                if jid not in existing[project.id]
                and (not tracks_of[jid] or project.track_id in tracks_of[jid])
                and not conflicted(users_of[jid], project)
            ]
            if not candidates:
                short.append(project.id)
                break
            lightest = min(load[j] for j in candidates)
            pick = rng.choice([j for j in candidates if load[j] == lightest])
            JudgeAssignment.objects.create(event=event, judge_id=pick, project=project, batch=batch)
            existing[project.id].add(pick)
            load[pick] += 1
            created += 1
    record(
        "assignment.balanced",
        actor=user,
        event=event,
        detail={"batch": batch, "created": created, "reviews_per_project": k, "short_projects": short, "seed": seed},
    )
    return {"batch": batch, "created": created, "short_projects": short, "load": load}


@transaction.atomic
def assign_manual(event: Event, user, judge_user, project: Project, batch: str = "manual") -> JudgeAssignment:
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can assign judges.")
    if not EventRole.objects.filter(event=event, user=judge_user, role=Role.JUDGE).exists():
        raise ValidationError("That user is not a judge in this event.")
    if conflicted(judge_user, project):
        raise ValidationError("That judge is on the project's team.")
    assignment, created = JudgeAssignment.objects.get_or_create(
        event=event, judge=judge_user, project=project, defaults={"batch": batch}
    )
    if created:
        record("assignment.manual", actor=user, event=event, target=assignment)
    return assignment


@transaction.atomic
def unassign(assignment: JudgeAssignment, user):
    if not is_organizer(user, assignment.event):
        raise PermissionDenied("Only organizers can remove assignments.")
    if assignment.status == JudgeAssignment.Status.SUBMITTED:
        raise ValidationError("A submitted review cannot be unassigned; hide the project instead.")
    record("assignment.remove", actor=user, event=assignment.event, target=assignment)
    assignment.delete()


# --- scoring ----------------------------------------------------------------


def assignments_for_judge(judge_user, event: Event | None = None):
    """Only this judge's own assignments, further limited to their tracks."""
    qs = JudgeAssignment.objects.filter(judge=judge_user).select_related("project", "project__track", "event")
    if event is not None:
        qs = qs.filter(event=event)
        allowed = judge_track_ids(judge_user, event)
        if allowed is not None:
            qs = qs.filter(project__track_id__in=allowed)
    return qs


def get_own_assignment(judge_user, assignment_id: int) -> JudgeAssignment:
    """The only way to load an assignment for scoring. Filtering by judge here
    is what makes another judge's URL return 404 rather than their ballot."""
    try:
        return assignments_for_judge(judge_user).get(pk=assignment_id)
    except JudgeAssignment.DoesNotExist:
        raise PermissionDenied("That review is not yours.") from None


@transaction.atomic
def save_scores(
    assignment: JudgeAssignment, user, values: dict[str, int], comment: str = "", submit: bool = False
) -> JudgeAssignment:
    if assignment.judge_id != user.id:
        raise PermissionDenied("You can only score your own assignments.")
    event = assignment.event
    if not event.judging_open():
        raise PermissionDenied("Judging is not open for this event.")
    if assignment.status == JudgeAssignment.Status.SUBMITTED and not is_organizer(user, event):
        raise ValidationError("This review was already submitted.")
    rubric = ensure_rubric(event)
    criteria = {c.key: c for c in rubric.criteria.all()}
    cleaned = {}
    for key, value in values.items():
        if key not in criteria or value in (None, ""):
            continue
        v = int(value)
        if not rubric.scale_min <= v <= rubric.scale_max:
            raise ValidationError(f"{criteria[key].name} must be between {rubric.scale_min} and {rubric.scale_max}.")
        cleaned[key] = v
    if submit and set(cleaned) != set(criteria):
        missing = [criteria[k].name for k in criteria if k not in cleaned]
        raise ValidationError("Every criterion needs a score before submitting: " + ", ".join(missing))
    for key, v in cleaned.items():
        Score.objects.update_or_create(assignment=assignment, criterion=criteria[key], defaults={"value": v})
    assignment.comment = comment or ""
    if submit:
        assignment.mark_submitted()
    elif assignment.status == JudgeAssignment.Status.PENDING:
        assignment.status = JudgeAssignment.Status.IN_PROGRESS
    assignment.save()
    record(
        "score.submit" if submit else "score.save",
        actor=user,
        event=event,
        target=assignment,
        detail={"scores": cleaned, "has_comment": bool(comment)},
    )
    return assignment


# --- normalization and results ----------------------------------------------


def collect_reviews(event: Event) -> tuple[list[Review], dict[str, float], Rubric]:
    rubric = ensure_rubric(event)
    weights = {c.key: float(c.weight) for c in rubric.criteria.all()}
    reviews = []
    qs = JudgeAssignment.objects.filter(
        event=event, status=JudgeAssignment.Status.SUBMITTED, project__in=eligible_projects(event)
    ).prefetch_related("scores__criterion")
    for a in qs:
        values = {s.criterion.key: s.value for s in a.scores.all()}
        score = weighted_score(values, weights)
        if score is None:
            continue
        reviews.append(Review(judge_id=str(a.judge_id), project_id=str(a.project_id), score=score))
    return reviews, weights, rubric


@transaction.atomic
def recompute_results(event: Event, user) -> dict:
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can compute results.")
    reviews, _, rubric = collect_reviews(event)
    result = normalize(reviews, rubric.scale_min, rubric.scale_max)

    from community.services import tally

    community = tally(event) if event.voting_access != Event.VotingAccess.CLOSED else {}
    ProjectResult.objects.filter(event=event).delete()
    JudgeCalibration.objects.filter(event=event).delete()
    for project in eligible_projects(event):
        standing = result.projects.get(str(project.id))
        ProjectResult.objects.create(
            event=event,
            project=project,
            review_count=standing.n if standing else 0,
            raw_mean=standing.raw_mean if standing else None,
            normalized_mean=standing.normalized if standing else None,
            rank_raw=standing.rank_raw if standing else None,
            rank_normalized=standing.rank_normalized if standing else None,
            community_score=float(community[project.id]["votes"]) if project.id in community else None,
            method=result.method,
        )
    for judge_id, js in result.judges.items():
        JudgeCalibration.objects.create(
            event=event,
            judge_id=int(judge_id),
            review_count=js.n,
            mean=js.mean,
            stdev=js.stdev,
            shrink_weight=js.shrink_weight,
            flat=js.flat,
        )
    record(
        "results.recompute",
        actor=user,
        event=event,
        detail={"reviews": len(reviews), "projects": len(result.projects), "method": result.method},
    )
    return {"reviews": len(reviews), "projects": len(result.projects), "method": result.method}


@transaction.atomic
def publish_results(event: Event, user, publish: bool = True):
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can publish results.")
    event.results_published_at = timezone.now() if publish else None
    event.save(update_fields=["results_published_at", "updated_at"])
    record("results.publish" if publish else "results.unpublish", actor=user, event=event, target=event)


def progress(event: Event) -> list[dict]:
    """Per-judge completion, for the live dashboard."""
    rows = []
    counts = (
        JudgeAssignment.objects.filter(event=event)
        .values("judge_id", "judge__username", "judge__first_name", "judge__last_name")
        .annotate(
            total=Count("id"),
            submitted=Count("id", filter=Q(status=JudgeAssignment.Status.SUBMITTED)),
            in_progress=Count("id", filter=Q(status=JudgeAssignment.Status.IN_PROGRESS)),
        )
        .order_by("judge__username")
    )
    for c in counts:
        name = (c["judge__first_name"] + " " + c["judge__last_name"]).strip() or c["judge__username"]
        rows.append(
            {
                "judge_id": c["judge_id"],
                "name": name,
                "total": c["total"],
                "submitted": c["submitted"],
                "in_progress": c["in_progress"],
                "pending": c["total"] - c["submitted"] - c["in_progress"],
                "pct": round(100 * c["submitted"] / c["total"]) if c["total"] else 0,
            }
        )
    return rows


def criterion_means(project: Project) -> list[dict]:
    """Mean of each criterion over the submitted reviews of one project."""
    rubric = ensure_rubric(project.event)
    rows = []
    for criterion in rubric.criteria.all():
        values = list(
            Score.objects.filter(
                criterion=criterion, assignment__project=project, assignment__status=JudgeAssignment.Status.SUBMITTED
            ).values_list("value", flat=True)
        )
        rows.append(
            {
                "key": criterion.key,
                "name": criterion.name,
                "weight": criterion.weight,
                "n": len(values),
                "mean": (sum(values) / len(values)) if values else None,
                "pct": round(
                    100 * (sum(values) / len(values) - rubric.scale_min) / max(1, rubric.scale_max - rubric.scale_min)
                )
                if values
                else 0,
            }
        )
    return rows


def feedback_for(project: Project) -> list[dict]:
    """Written feedback for a team, without saying which judge wrote what.
    Judges are numbered in a stable order that is not the order they were
    assigned or the order they submitted in."""
    import hashlib

    reviews = JudgeAssignment.objects.filter(project=project, status=JudgeAssignment.Status.SUBMITTED).prefetch_related(
        "scores__criterion"
    )
    ordered = sorted(reviews, key=lambda a: hashlib.sha256(f"{project.pk}:{a.judge_id}".encode()).hexdigest())
    return [
        {
            "label": f"Judge {i}",
            "comment": a.comment,
            "scores": {s.criterion.key: s.value for s in a.scores.all()},
        }
        for i, a in enumerate(ordered, start=1)
    ]
