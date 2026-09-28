"""Judging operations: rubric setup, assignment, scoring, normalization runs,
result publication. All role checks happen here or in events.permissions."""

from __future__ import annotations

import random
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_slug
from django.db import transaction
from django.db.models import Avg, Count, F, Q
from django.utils import timezone

from audit.services import record
from events.models import Event, EventRole, Project, Role, TeamMembership
from events.permissions import is_organizer, judge_track_ids
from plumbline.inputs import as_int

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
    cleaned = clean_criteria(rows)
    rubric.criteria.all().delete()
    for i, row in enumerate(cleaned):
        Criterion.objects.create(rubric=rubric, order=i, **row)
    record("rubric.replace", actor=user, event=rubric.event, target=rubric, detail={"criteria": cleaned})


def clean_criteria(rows: list[dict]) -> list[dict]:
    """A rubric a judge can actually score with: at least one criterion,
    distinct keys, and weights that add up to something."""
    if not rows:
        raise ValidationError("A rubric needs at least one criterion.")
    if len(rows) > 30:
        raise ValidationError("A rubric can have at most 30 criteria.")
    cleaned, seen = [], set()
    for row in rows:
        key = str(row.get("key") or "").strip().lower()
        name = str(row.get("name") or "").strip()
        try:
            validate_slug(key)
        except ValidationError:
            raise ValidationError(
                f"'{key}' is not a usable key: letters, digits, hyphens and underscores only."
            ) from None
        if len(key) > 40:
            raise ValidationError(f"The key '{key}' is longer than 40 characters.")
        if key in seen:
            raise ValidationError(f"The key '{key}' is used twice. Each criterion needs its own.")
        seen.add(key)
        if not name or len(name) > 120:
            raise ValidationError(f"Criterion '{key}' needs a name of at most 120 characters.")
        try:
            weight = Decimal(str(row.get("weight", 1) if row.get("weight") is not None else 1))
        except (InvalidOperation, ValueError):
            raise ValidationError(f"The weight of '{key}' is not a number.") from None
        if not weight.is_finite() or weight < 0 or weight > Decimal("9999.99"):
            raise ValidationError(f"The weight of '{key}' must be between 0 and 9999.99.")
        cleaned.append(
            {
                "key": key,
                "name": name,
                "description": str(row.get("description") or ""),
                "weight": weight.quantize(Decimal("0.01")),
            }
        )
    if not any(row["weight"] > 0 for row in cleaned):
        raise ValidationError("At least one criterion needs a weight above zero.")
    return cleaned


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
    k = as_int(reviews_per_project or event.reviews_per_project, "reviews_per_project", 1, 20)
    batch = (batch or timezone.now().strftime("batch-%Y%m%d-%H%M")).strip()
    if len(batch) > 40:
        raise ValidationError("A batch label can be at most 40 characters.")
    if seed is not None:
        seed = as_int(seed, "seed", 0, 2**31 - 1)
    rng = random.Random(seed)

    # In a fixed order. The database returns rows in whatever order suits
    # it, and the same seed has to give the same assignment on any of them.
    judge_roles = sorted(judges_for(event), key=lambda r: r.user_id)
    if not judge_roles:
        raise ValidationError("This event has no judges yet.")
    load = {r.user_id: 0 for r in judge_roles}
    for row in JudgeAssignment.objects.filter(event=event).values("judge_id").annotate(n=Count("id")):
        # Reviews by someone who is no longer a judge still count toward a
        # project's total, but that person gets no new work.
        if row["judge_id"] in load:
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
    if project.event_id != event.id:
        raise ValidationError("That project belongs to a different event.")
    if not eligible_projects(event).filter(pk=project.pk).exists():
        raise ValidationError("Only submitted projects that are not hidden or marked as duplicates can be assigned.")
    allowed = judge_track_ids(judge_user, event)
    if allowed is not None and project.track_id not in allowed:
        raise ValidationError("That judge is restricted to other tracks.")
    if conflicted(judge_user, project):
        raise ValidationError("That judge is on the project's team.")
    batch = (batch or "manual").strip()
    if len(batch) > 40:
        raise ValidationError("A batch label can be at most 40 characters.")
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
    """Only this judge's own assignments, limited to the tracks they hold in
    each event, whether or not the caller names an event. A project that has
    been withdrawn or hidden since it was assigned drops out of the queue;
    a review already submitted stays on the record."""
    qs = JudgeAssignment.objects.filter(judge=judge_user).select_related("project", "project__track", "event")
    roles = EventRole.objects.filter(user=judge_user, role=Role.JUDGE).prefetch_related("tracks")
    if event is not None:
        qs = qs.filter(event=event)
        roles = roles.filter(event=event)
    visible = Q(pk__in=[])
    for role in roles:
        track_ids = [t.id for t in role.tracks.all()]
        if track_ids:
            visible |= Q(event_id=role.event_id, project__track_id__in=track_ids)
        else:
            visible |= Q(event_id=role.event_id)
    current = Q(project__status=Project.Status.SUBMITTED, project__is_hidden=False, project__duplicate_of__isnull=True)
    return qs.filter(visible).filter(Q(status=JudgeAssignment.Status.SUBMITTED) | current)


def get_own_assignment(judge_user, assignment_id: int, event: Event | None = None) -> JudgeAssignment:
    """The only way to load an assignment for scoring. Filtering by judge and
    track here is what makes another judge's URL a refusal rather than their
    ballot. The answer is the same whether the id exists or not."""
    try:
        return assignments_for_judge(judge_user, event).get(pk=assignment_id)
    except (JudgeAssignment.DoesNotExist, OverflowError):
        raise PermissionDenied("That review is not yours.") from None


@transaction.atomic
def save_scores(
    assignment: JudgeAssignment, user, values: dict[str, int], comment: str | None = "", submit: bool = False
) -> JudgeAssignment:
    """Save a draft or submit. `comment=None` leaves the stored comment alone.

    The row is locked and read again first: a draft saved by the page a moment
    before the judge pressed Submit can reach the server after the submit
    did, and must find the review closed."""
    if assignment.judge_id != user.id:
        raise PermissionDenied("You can only score your own assignments.")
    assignment = JudgeAssignment.objects.select_for_update().select_related("event", "project").get(pk=assignment.pk)
    event = assignment.event
    if not event.judging_open():
        raise PermissionDenied("Judging is not open for this event.")
    if assignment.status == JudgeAssignment.Status.SUBMITTED:
        # For everyone, organizers included: the page says a submitted review
        # is closed, and the server agrees with the page.
        raise ValidationError("This review was already submitted.")
    if not assignments_for_judge(user, event).filter(pk=assignment.pk).exists():
        raise PermissionDenied("This project is outside your tracks, or is no longer in the running.")
    if conflicted(user, assignment.project):
        # Assignment refuses a judge's own team; this is for the judge who
        # joined the team after being assigned.
        raise PermissionDenied("This is your own team's project. Ask the organizers to give it to another judge.")
    if len(comment or "") > 10000:
        raise ValidationError("Comments are limited to 10,000 characters.")
    rubric = ensure_rubric(event)
    criteria = {c.key: c for c in rubric.criteria.all()}
    cleaned = {}
    for key, value in (values or {}).items():
        if key not in criteria or value in (None, ""):
            continue
        v = as_int(value, criteria[key].name, rubric.scale_min, rubric.scale_max)
        cleaned[key] = v
    if submit and set(cleaned) != set(criteria):
        missing = [criteria[k].name for k in criteria if k not in cleaned]
        raise ValidationError("Every criterion needs a score before submitting: " + ", ".join(missing))
    for key, v in cleaned.items():
        Score.objects.update_or_create(assignment=assignment, criterion=criteria[key], defaults={"value": v})
    if comment is not None:
        assignment.comment = comment
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
        detail={"scores": cleaned, "has_comment": bool(assignment.comment)},
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
    result = normalize(reviews, rubric.scale_min, rubric.scale_max, jury_k=rubric.effective_jury_k())
    by_criterion = criterion_means_for_event(event, rubric)

    from community.services import tally

    community = tally(event) if event.voting_access != Event.VotingAccess.CLOSED else {}
    pairs = None
    if rubric.pairwise:
        from .pairwise_services import compute

        pairs = compute(event)
    ProjectResult.objects.filter(event=event).delete()
    JudgeCalibration.objects.filter(event=event).delete()
    for project in eligible_projects(event):
        standing = result.projects.get(str(project.id))
        versus = pairs.projects.get(str(project.id)) if pairs else None
        heard = versus is not None and versus.comparisons > 0
        ProjectResult.objects.create(
            pairwise_score=versus.score if heard else None,
            pairwise_rank=versus.rank if heard else None,
            pairwise_n=versus.comparisons if heard else 0,
            pairwise_wins=versus.wins if heard else 0,
            event=event,
            project=project,
            review_count=standing.n if standing else 0,
            raw_mean=standing.raw_mean if standing else None,
            normalized_mean=standing.normalized if standing else None,
            rank_raw=standing.rank_raw if standing else None,
            rank_normalized=standing.rank_normalized if standing else None,
            adjusted_mean=standing.adjusted if standing else None,
            rank=standing.rank if standing else None,
            criterion_means=by_criterion.get(project.id, []),
            jury_k=result.jury_k,
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
            shrunk_mean=js.shrunk_mean,
            shrunk_stdev=js.shrunk_stdev,
            flat=js.flat,
        )
    summary = {
        "reviews": len(reviews),
        "projects": len(result.projects),
        "method": result.method,
        "judge_k": result.shrink_k,
        "jury_k": result.jury_k,
        "panel_mean": result.panel_mean,
        "panel_stdev": result.panel_stdev,
        "comparisons": pairs.comparisons if pairs else 0,
    }
    record("results.recompute", actor=user, event=event, detail=summary)
    return summary


def criterion_means_for_event(event: Event, rubric: Rubric) -> dict[int, list[dict]]:
    """Per project, the mean of each criterion over its submitted reviews, in
    rubric order. One query for the whole event."""
    criteria = list(rubric.criteria.all())
    rows = (
        Score.objects.filter(
            criterion__rubric=rubric,
            assignment__event=event,
            assignment__status=JudgeAssignment.Status.SUBMITTED,
        )
        .values("assignment__project_id", "criterion_id")
        .annotate(mean=Avg("value"), n=Count("id"))
    )
    found = {(r["assignment__project_id"], r["criterion_id"]): r for r in rows}
    span = max(1, rubric.scale_max - rubric.scale_min)
    out: dict[int, list[dict]] = {}
    for project_id in {k[0] for k in found}:
        out[project_id] = []
        for c in criteria:
            r = found.get((project_id, c.id))
            out[project_id].append(
                {
                    "key": c.key,
                    "name": c.name,
                    "weight": float(c.weight),
                    "n": r["n"] if r else 0,
                    "mean": round(r["mean"], 4) if r else None,
                    "pct": round(100 * (r["mean"] - rubric.scale_min) / span) if r else 0,
                }
            )
    return out


@transaction.atomic
def publish_results(event: Event, user, publish: bool = True):
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can publish results.")
    if publish and not ProjectResult.objects.filter(event=event, rank__isnull=False).exists():
        raise ValidationError("There is nothing to publish yet. Compute the results first.")
    event.results_published_at = timezone.now() if publish else None
    event.save(update_fields=["results_published_at", "updated_at"])
    record("results.publish" if publish else "results.unpublish", actor=user, event=event, target=event)


def standings(event: Event):
    """The stored results, minus any project that has left the running since
    they were computed. Hiding a project takes it off the published page at
    once; it does not wait for the next recompute."""
    return (
        ProjectResult.objects.filter(event=event, project__in=eligible_projects(event))
        .select_related("project", "project__track", "project__team")
        .order_by(F("rank").asc(nulls_last=True), F("rank_normalized").asc(nulls_last=True), "id")
    )


def placed(event: Event, track_id: int | None = None) -> list[ProjectResult]:
    """The standings as a visitor sees them, each row with its place among
    the rows shown. A project hidden after the results were computed leaves
    no gap, and within a track places are counted within that track. Ties
    share a place. `place_raw` is the same count on the raw order."""
    rows = list(standings(event))
    if track_id is not None:
        rows = [r for r in rows if r.project.track_id == track_id]
    ranked = [r for r in rows if r.rank]
    ranks = sorted(r.rank for r in ranked)
    raws = sorted(r.rank_raw for r in ranked if r.rank_raw)
    from bisect import bisect_left

    for r in rows:
        r.place = bisect_left(ranks, r.rank) + 1 if r.rank else None
        r.place_raw = bisect_left(raws, r.rank_raw) + 1 if r.rank and r.rank_raw else None
        r.of = len(ranked)
        r.shift = (r.place_raw - r.place) if r.place and r.place_raw else None
    return rows


def computed_jury_k(event: Event) -> float | None:
    """The jury-size constant the stored results were computed with."""
    row = ProjectResult.objects.filter(event=event).exclude(jury_k__isnull=True).first()
    return row.jury_k if row else None


@transaction.atomic
def set_jury_k(rubric: Rubric, user, value) -> Rubric:
    """Change how strongly projects with few reviews are pulled toward the
    panel mean. None follows the event's reviews per project; 0 is off."""
    if not is_organizer(user, rubric.event):
        raise PermissionDenied("Only organizers can edit the rubric.")
    new = None if value in (None, "") else as_int(value, "Jury-size adjustment", 0, 100)
    if new != rubric.jury_k:
        record("rubric.jury_k", actor=user, event=rubric.event, target=rubric, detail={"changed": [rubric.jury_k, new]})
        rubric.jury_k = new
        rubric.save(update_fields=["jury_k"])
    return rubric


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
