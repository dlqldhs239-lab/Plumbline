"""Judge console. Every query goes through assignments_for_judge, so a judge
can only ever load, see or score their own assignments."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from events.models import Event, Role

from . import services
from .models import JudgeAssignment
from .normalization import weighted_score

SUBMITTED = JudgeAssignment.Status.SUBMITTED


def _own(user, event):
    """This judge's assignments in the order they are worked through, each
    with its own weighted score so far. Nothing here comes from another judge."""
    rubric = services.ensure_rubric(event)
    criteria = list(rubric.criteria.all())
    weights = {c.key: float(c.weight) for c in criteria}
    by_id = {c.id: c.key for c in criteria}
    items = list(
        services.assignments_for_judge(user, event)
        .select_related("project__team")
        .prefetch_related("scores")
        .order_by("batch", "id")
    )
    for a in items:
        values = {by_id[s.criterion_id]: s.value for s in a.scores.all() if s.criterion_id in by_id}
        a.filled = len(values)
        a.own_score = weighted_score(values, weights) if values else None
        a.done = a.status == SUBMITTED
    return rubric, criteria, items


def _own_marks(rubric, items) -> dict | None:
    """How this judge has marked so far, for their own eyes: it is easier to
    stay consistent over thirty projects with your own spread in view."""
    scores = [a.own_score for a in items if a.done and a.own_score is not None]
    if not scores:
        return None
    mean = sum(scores) / len(scores)
    steps = list(range(rubric.scale_min, rubric.scale_max + 1))
    counts = {v: 0 for v in steps}
    for s in scores:
        counts[min(steps, key=lambda v: (abs(v - s), -v))] += 1
    most = max(counts.values()) or 1
    return {
        "n": len(scores),
        "mean": mean,
        "low": min(scores),
        "high": max(scores),
        "bins": [{"value": v, "n": counts[v], "pct": round(100 * counts[v] / most)} for v in steps],
    }


@login_required
def index(request):
    events = Event.objects.filter(roles__user=request.user, roles__role=Role.JUDGE).distinct()
    rows = []
    for ev in events:
        qs = services.assignments_for_judge(request.user, ev)
        total, done = qs.count(), qs.filter(status=SUBMITTED).count()
        rows.append({"event": ev, "total": total, "done": done, "left": total - done, "open": ev.judging_open()})
    rows.sort(key=lambda r: (not (r["open"] and r["left"]), r["event"].name))
    return render(request, "judging/index.html", {"rows": rows})


@login_required
def event_queue(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not event.roles.filter(user=request.user, role=Role.JUDGE).exists():
        raise PermissionDenied("You are not a judge in this event.")
    rubric, criteria, items = _own(request.user, event)
    todo = [a for a in items if not a.done]
    finished = [a for a in items if a.done]
    return render(
        request,
        "judging/queue.html",
        {
            "event": event,
            "rubric": rubric,
            "n_criteria": len(criteria),
            "items": [{"a": a, "filled": a.filled, "n": len(criteria)} for a in todo + finished],
            "todo": todo,
            "finished": finished,
            "done": len(finished),
            "total": len(items),
            "next_item": todo[0] if todo else None,
            "marks": _own_marks(rubric, items),
            "judging_open": event.judging_open(),
            "judging_starts": event.judging_open_at or event.submissions_close_at,
            "judging_not_started": not event.judging_open()
            and (event.judging_close_at is None or not _past(event.judging_close_at)),
        },
    )


@login_required
def review(request, slug, pk):
    event = get_object_or_404(Event, slug=slug)
    assignment = services.get_own_assignment(request.user, pk, event)
    if request.method == "POST":
        rubric = services.ensure_rubric(event)
        values = {c.key: request.POST.get(f"score_{c.key}") for c in rubric.criteria.all()}
        submit = "submit" in request.POST
        comment = request.POST.get("comment", "")
        try:
            services.save_scores(assignment, request.user, values, comment, submit=submit)
        except (ValidationError, PermissionDenied) as e:
            reason = "; ".join(getattr(e, "messages", [str(e)]))
            if submit:
                # Not submitted, but nothing the judge entered is thrown away.
                try:
                    services.save_scores(assignment, request.user, values, comment, submit=False)
                    reason += " What you entered is saved as a draft."
                except (ValidationError, PermissionDenied):
                    pass
            messages.error(request, reason)
            return redirect("judging:review", slug=slug, pk=pk)
        else:
            if submit:
                messages.success(request, f"Review of {assignment.project.title} submitted.")
                nxt = (
                    services.assignments_for_judge(request.user, event)
                    .exclude(status=SUBMITTED)
                    .order_by("batch", "id")
                    .first()
                )
                if nxt:
                    return redirect("judging:review", slug=slug, pk=nxt.pk)
                return redirect("judging:queue", slug=slug)
            messages.success(request, "Draft saved.")
            return redirect("judging:review", slug=slug, pk=pk)

    rubric, criteria, items = _own(request.user, event)
    current = next((a for a in items if a.id == assignment.id), assignment)
    ids = [a.id for a in items]
    pos = ids.index(assignment.id) if assignment.id in ids else -1
    existing = {s.criterion_id: s.value for s in assignment.scores.all()}
    for c in criteria:
        c.chosen = existing.get(c.id)
    project = assignment.project
    is_open = event.judging_open()
    return render(
        request,
        "judging/review.html",
        {
            "event": event,
            "assignment": assignment,
            "project": project,
            "rubric": rubric,
            "criteria": criteria,
            "existing": existing,
            "scale": list(range(rubric.scale_min, rubric.scale_max + 1)),
            "items": items,
            "pos": pos + 1,
            "total": len(ids),
            "done": sum(1 for a in items if a.done),
            "previous": items[pos - 1] if pos > 0 else None,
            "following": items[pos + 1] if 0 <= pos < len(items) - 1 else None,
            "own_score": getattr(current, "own_score", None),
            "locked": assignment.status == SUBMITTED or not is_open,
            "judging_open": is_open,
            "judging_starts": event.judging_open_at or event.submissions_close_at,
            "judging_not_started": not is_open
            and (event.judging_close_at is None or not _past(event.judging_close_at)),
            "answers": project.answers.select_related("question").order_by("question__order"),
        },
    )


def _past(moment) -> bool:
    from django.utils import timezone

    return moment <= timezone.now()
