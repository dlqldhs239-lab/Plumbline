"""Pairwise judging in the portal: which pair a judge is shown next, recording
what they preferred, and turning the comparisons into a ranking.

A judge only ever compares projects that are assigned to them, so the rules
that decide what a judge may see (their own assignments, their tracks) hold
here without being written a second time. A project of a team the judge has
joined since is left out. The judge answers the pair the portal shows, and
as many pairs as the portal asks for: which comparisons are made is never
the judge's choice.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections import defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from audit.services import record
from events.models import Event, EventRole, Role, TeamMembership
from events.permissions import is_organizer

from .models import JudgeAssignment, PairwiseComparison, ProjectResult
from .pairwise import Comparison, PairwiseResult, estimate, kendall_tau
from .services import assignments_for_judge, eligible_projects, ensure_rubric, placed

PAIRS_PER_PROJECT = 3  # a judge is asked about each of their projects about this often


def enabled(event: Event) -> bool:
    return ensure_rubric(event).pairwise


def pool(judge, event: Event) -> list:
    """The projects this judge may compare: the ones assigned to them that
    are still in the running, and not their own team's."""
    seen, out = set(), []
    live = set(eligible_projects(event).values_list("id", flat=True))
    own = set(TeamMembership.objects.filter(team__event=event, user=judge).values_list("team_id", flat=True))
    for a in assignments_for_judge(judge, event).select_related("project__team", "project__track"):
        if a.project.team_id in own:
            continue
        if a.project_id in live and a.project_id not in seen:
            seen.add(a.project_id)
            out.append(a.project)
    return sorted(out, key=lambda p: p.id)


def asked_for(n_projects: int) -> int:
    """How many comparisons a judge with n projects is asked for: every pair
    when there are few, about three per project when there are many."""
    every = n_projects * (n_projects - 1) // 2
    return min(every, max(0, PAIRS_PER_PROJECT * n_projects // 2 + n_projects % 2))


def _key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def done_by(judge, event: Event) -> set[tuple[int, int]]:
    rows = PairwiseComparison.objects.filter(event=event, judge=judge).values_list("left_id", "right_id")
    return {_key(a, b) for a, b in rows}


def status(judge, event: Event) -> dict:
    projects = pool(judge, event)
    live = {p.id for p in projects}
    done = {k for k in done_by(judge, event) if k[0] in live and k[1] in live}
    wanted = asked_for(len(projects))
    return {"projects": len(projects), "done": len(done), "asked": wanted, "left": max(0, wanted - len(done))}


def next_pair(judge, event: Event):
    """The pair to show next, or None when the judge has done what was asked.

    Of the pairs this judge has not compared, take the one whose two projects
    the judge has been asked about least, so that the judge hears about every
    project before hearing about any twice. Ties are broken by a hash of the
    judge and the pair: stable for one judge, different between judges.
    Which of the two is shown on the left is decided by the same hash, so no
    project gains from always standing first.

    The answer depends on this judge's own answers and projects and on
    nothing else, so the pair on the page is still the pair when the answer
    arrives, whatever other judges did in between."""
    projects = pool(judge, event)
    done = done_by(judge, event)
    live = {p.id for p in projects}
    if len({k for k in done if k[0] in live and k[1] in live}) >= asked_for(len(projects)):
        return None
    mine: dict[int, int] = defaultdict(int)
    for a, b in done:
        if a in live and b in live:
            mine[a] += 1
            mine[b] += 1
    best, best_rank = None, None
    for x in range(len(projects)):
        for y in range(x + 1, len(projects)):
            a, b = projects[x], projects[y]
            if _key(a.id, b.id) in done:
                continue
            digest = hashlib.sha256(f"{judge.pk}:{a.id}:{b.id}".encode()).hexdigest()
            rank = (max(mine[a.id], mine[b.id]), mine[a.id] + mine[b.id], digest)
            if best_rank is None or rank < best_rank:
                best, best_rank = (a, b, digest), rank
    if best is None:
        return None
    a, b, digest = best
    return (a, b) if int(digest[:2], 16) % 2 == 0 else (b, a)


@transaction.atomic
def record_comparison(judge, event: Event, first_id: int, second_id: int, preferred_id: int | None):
    """Record that the judge preferred one of two projects, or neither."""
    if not enabled(event):
        raise PermissionDenied("This event does not use pairwise judging.")
    if not EventRole.objects.filter(event=event, user=judge, role=Role.JUDGE).exists():
        raise PermissionDenied("You are not a judge in this event.")
    if not event.judging_open():
        raise PermissionDenied("Judging is not open for this event.")
    if first_id == second_id:
        raise ValidationError("A project cannot be compared with itself.")
    allowed = {p.id: p for p in pool(judge, event)}
    if first_id not in allowed or second_id not in allowed:
        raise PermissionDenied("You can only compare projects that are assigned to you.")
    if preferred_id not in (None, first_id, second_id):
        raise ValidationError("The preferred project must be one of the two.")
    left, right = _key(first_id, second_id)
    # One answer per judge and pair. Asking twice would let a judge vote twice.
    from django.contrib.auth import get_user_model

    get_user_model().objects.select_for_update().filter(pk=judge.pk).first()
    if PairwiseComparison.objects.filter(judge=judge, left_id=left, right_id=right).exists():
        raise ValidationError("You have already compared these two.")
    # Which pairs, and how many, is the portal's decision. A judge who could
    # choose would weigh more than one who answered what they were shown.
    offered = next_pair(judge, event)
    if offered is None:
        raise ValidationError("You have made every comparison you were asked for.")
    if _key(offered[0].id, offered[1].id) != (left, right):
        raise ValidationError("That is not the pair you were shown. Answer the pair on your page.")
    try:
        with transaction.atomic():
            row = PairwiseComparison.objects.create(
                event=event, judge=judge, left_id=left, right_id=right, preferred_id=preferred_id
            )
    except IntegrityError:
        # The same answer sent twice at once: the first one stands.
        raise ValidationError("You have already compared these two.") from None
    record(
        "comparison.submit",
        actor=judge,
        event=event,
        target=row,
        detail={"left": left, "right": right, "preferred": preferred_id},
    )
    return row


def collect(event: Event) -> list[Comparison]:
    """Every recorded comparison between two projects still in the running,
    made by a judge to whom both are still assigned. Taking a project from a
    judge, or the judge from the event, takes their comparisons out of the
    count as it takes their scores."""
    live = set(eligible_projects(event).values_list("id", flat=True))
    assigned = set(JudgeAssignment.objects.filter(event=event).values_list("judge_id", "project_id"))
    out = []
    rows = PairwiseComparison.objects.filter(event=event).order_by("id")
    for judge_id, left, right, preferred in rows.values_list("judge_id", "left_id", "right_id", "preferred_id"):
        if left not in live or right not in live:
            continue
        if (judge_id, left) not in assigned or (judge_id, right) not in assigned:
            continue
        if preferred is None:
            out.append(Comparison(str(left), str(right), str(judge_id), tie=True))
        else:
            other = right if preferred == left else left
            out.append(Comparison(str(preferred), str(other), str(judge_id)))
    return out


def compute(event: Event) -> PairwiseResult:
    ids = [str(i) for i in eligible_projects(event).order_by("id").values_list("id", flat=True)]
    return estimate(collect(event), ids)


def summary(event: Event, user) -> dict | None:
    """The pairwise ranking beside the rubric one, for organizers."""
    if not is_organizer(user, event):
        raise PermissionDenied("Organizer role required.")
    if not enabled(event):
        return None
    rows = ranking(event)
    both = [r for r in rows if r.adjusted_mean is not None and r.pairwise_score is not None]
    tau = kendall_tau(
        {str(r.project_id): r.pairwise_score for r in both}, {str(r.project_id): r.adjusted_mean for r in both}
    )
    counted = collect(event)
    live = estimate(counted, [str(i) for i in eligible_projects(event).order_by("id").values_list("id", flat=True)])
    # Stale is judged project by project: one comparison gone and another
    # arrived leaves the total where it was and the ranking out of date.
    now = {int(pid): s.comparisons for pid, s in live.projects.items() if s.comparisons}
    then = {r.project_id: r.pairwise_n for r in rows}
    return {
        "rows": rows,
        "comparisons": live.comparisons,
        "judges": len({c.judge for c in counted}),
        "tau": tau,
        "components": live.components,
        "unheard": len(live.unheard),
        "stale": now != then,
    }


def ranking(event: Event) -> list:
    """The stored pairwise ranking of the projects still in the running, each
    row with its place among the rows shown (`pairwise_place`) and its place
    in the rubric ranking as the results page gives it (`rubric_place`)."""
    rows = list(
        ProjectResult.objects.filter(event=event, pairwise_n__gt=0, project__in=eligible_projects(event))
        .select_related("project", "project__team")
        .order_by("pairwise_rank", "id")
    )
    ranks = sorted(r.pairwise_rank for r in rows if r.pairwise_rank)
    rubric = {r.project_id: r.place for r in placed(event)}
    for r in rows:
        r.pairwise_place = bisect_left(ranks, r.pairwise_rank) + 1 if r.pairwise_rank else None
        r.rubric_place = rubric.get(r.project_id)
    return rows


@transaction.atomic
def set_enabled(event: Event, user, on: bool):
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can change how an event is judged.")
    rubric = ensure_rubric(event)
    if rubric.pairwise != bool(on):
        rubric.pairwise = bool(on)
        rubric.save(update_fields=["pairwise"])
        record("rubric.pairwise", actor=user, event=event, target=rubric, detail={"enabled": rubric.pairwise})
    return rubric
