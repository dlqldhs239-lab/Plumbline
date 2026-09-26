"""Judge console. Every query goes through assignments_for_judge, so a judge
can only ever load, see or score their own assignments."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from events.models import Event, Role

from . import services
from .models import JudgeAssignment


@login_required
def index(request):
    events = Event.objects.filter(roles__user=request.user, roles__role=Role.JUDGE).distinct()
    rows = []
    for ev in events:
        qs = services.assignments_for_judge(request.user, ev)
        rows.append({"event": ev, "total": qs.count(), "done": qs.filter(status=JudgeAssignment.Status.SUBMITTED).count()})
    return render(request, "judging/index.html", {"rows": rows})


@login_required
def event_queue(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not event.roles.filter(user=request.user, role=Role.JUDGE).exists():
        raise PermissionDenied("You are not a judge in this event.")
    qs = services.assignments_for_judge(request.user, event).prefetch_related("scores").order_by("status", "batch", "id")
    rubric = services.ensure_rubric(event)
    n_criteria = rubric.criteria.count()
    items = []
    for a in qs:
        items.append({"a": a, "filled": a.scores.count(), "n": n_criteria})
    done = sum(1 for i in items if i["a"].status == JudgeAssignment.Status.SUBMITTED)
    next_item = next((i["a"] for i in items if i["a"].status != JudgeAssignment.Status.SUBMITTED), None)
    return render(
        request,
        "judging/queue.html",
        {"event": event, "items": items, "done": done, "total": len(items), "next_item": next_item, "judging_open": event.judging_open()},
    )


@login_required
def review(request, slug, pk):
    event = get_object_or_404(Event, slug=slug)
    assignment = services.get_own_assignment(request.user, pk)
    if assignment.event_id != event.id:
        raise PermissionDenied("That review is not yours.")
    rubric = services.ensure_rubric(event)
    criteria = list(rubric.criteria.all())
    existing = {s.criterion_id: s.value for s in assignment.scores.all()}
    if request.method == "POST":
        values = {c.key: request.POST.get(f"score_{c.key}") for c in criteria}
        submit = "submit" in request.POST
        try:
            services.save_scores(assignment, request.user, values, request.POST.get("comment", ""), submit=submit)
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
        else:
            if submit:
                messages.success(request, f"Review of {assignment.project.title} submitted.")
                nxt = (
                    services.assignments_for_judge(request.user, event)
                    .exclude(status=JudgeAssignment.Status.SUBMITTED)
                    .order_by("batch", "id")
                    .first()
                )
                if nxt:
                    return redirect("judging:review", slug=slug, pk=nxt.pk)
                return redirect("judging:queue", slug=slug)
            messages.success(request, "Draft saved.")
            return redirect("judging:review", slug=slug, pk=pk)
    project = assignment.project
    scale = list(range(rubric.scale_min, rubric.scale_max + 1))
    queue = services.assignments_for_judge(request.user, event).order_by("batch", "id")
    ids = list(queue.values_list("id", flat=True))
    pos = ids.index(assignment.id) + 1 if assignment.id in ids else 0
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
            "scale": scale,
            "pos": pos,
            "total": len(ids),
            "judging_open": event.judging_open(),
            "answers": project.answers.select_related("question").order_by("question__order"),
        },
    )
