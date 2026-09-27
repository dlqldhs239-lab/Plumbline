"""Console pages for prizes and the organizer's own form questions."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_exempt

from judging import services as judging_services
from plumbline.inputs import id_or_404

from . import extras, importer
from .models import CustomQuestion, Event, Prize, Project
from .permissions import is_organizer


def _event(request, slug):
    event = get_object_or_404(Event.objects.prefetch_related("tracks"), slug=slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    return event


@login_required
def organize_prizes(request, slug):
    event = _event(request, slug)
    editing = None
    if request.GET.get("edit"):
        editing = get_object_or_404(Prize, pk=id_or_404(request.GET["edit"]), event=event)
    entered = None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "delete":
                prize = get_object_or_404(Prize, pk=id_or_404(request.POST.get("prize")), event=event)
                extras.delete_prize(prize, request.user)
                messages.info(request, f"{prize.name} removed.")
            else:
                prize = None
                if request.POST.get("prize"):
                    prize = get_object_or_404(Prize, pk=id_or_404(request.POST["prize"]), event=event)
                saved = extras.save_prize(event, request.user, request.POST, prize)
                messages.success(request, f"{saved.name} saved.")
            return redirect("organize_prizes", slug=slug)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            entered = request.POST
    return render(
        request,
        "events/organize/prizes.html",
        {
            "event": event,
            "prizes": event.prizes.select_related("track"),
            "editing": editing,
            "entered": entered,
        },
    )


@login_required
def organize_questions(request, slug):
    event = _event(request, slug)
    editing = None
    if request.GET.get("edit"):
        editing = get_object_or_404(CustomQuestion, pk=id_or_404(request.GET["edit"]), event=event)
    entered = None
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "delete":
                q = get_object_or_404(CustomQuestion, pk=id_or_404(request.POST.get("question")), event=event)
                extras.delete_question(q, request.user)
                messages.info(request, "Question removed, with the answers given to it.")
            else:
                q = None
                if request.POST.get("question"):
                    q = get_object_or_404(CustomQuestion, pk=id_or_404(request.POST["question"]), event=event)
                extras.save_question(event, request.user, request.POST, q)
                messages.success(request, "Question saved. It is on the submission form now.")
            return redirect("organize_questions", slug=slug)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            entered = request.POST
    questions = list(event.custom_questions.all())
    for q in questions:
        q.answered = q.answers.exclude(value="").count()
    return render(
        request,
        "events/organize/questions.html",
        {
            "event": event,
            "questions": questions,
            "kinds": CustomQuestion.Kind.choices,
            "editing": editing,
            "entered": entered,
            "projects": event.projects.count(),
        },
    )


@login_required
def organize_import(request, slug):
    """Read a CSV, show what it would do, and only then do it."""
    event = _event(request, slug)
    kind = request.POST.get("kind") or request.GET.get("kind") or "projects"
    if kind not in importer.COLUMNS:
        kind = "projects"
    if request.GET.get("template"):
        response = HttpResponse(importer.template(kind), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{event.slug}-{kind}-template.csv"'
        return response
    text, checked = "", None
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is not None:
            if upload.size > importer.MAX_BYTES:
                messages.error(request, "The file is larger than 2 MB. Split it.")
                return redirect("organize_import", slug=slug)
            text = upload.read().decode("utf-8-sig", errors="replace")
        else:
            text = request.POST.get("text", "")
        try:
            if request.POST.get("action") == "apply":
                made = importer.apply(event, request.user, text, kind)
                if kind == "judges":
                    messages.success(request, f"{made['judges']} judge{'s' if made['judges'] != 1 else ''} imported.")
                    return redirect("organize_judges", slug=slug)
                messages.success(
                    request,
                    f"{made['projects']} project{'s' if made['projects'] != 1 else ''} imported, in {made['teams']} "
                    f"new team{'s' if made['teams'] != 1 else ''}, with {made['accounts']} new "
                    f"account{'s' if made['accounts'] != 1 else ''}.",
                )
                return redirect("organize_projects", slug=slug)
            checked = importer.plan(event, request.user, text, kind)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    return render(
        request,
        "events/organize/import.html",
        {"event": event, "kind": kind, "text": text, "checked": checked, "columns": importer.COLUMNS[kind]},
    )


EMBED_LIMIT = 60


@xframe_options_exempt
def embed(request, slug, what):
    """The gallery or the published standings with nothing around them, to
    sit in a frame on the event's own site. Public data only."""
    event = get_object_or_404(Event.objects.prefetch_related("tracks"), slug=slug)
    if what not in ("gallery", "results"):
        raise PermissionDenied("Nothing to embed at that address.")
    try:
        limit = max(1, min(EMBED_LIMIT, int(request.GET.get("limit", "12"))))
    except ValueError:
        limit = 12
    track = request.GET.get("track") or ""
    track_id = None
    if track:
        track_id = id_or_404(track)
        get_object_or_404(event.tracks, pk=track_id)
    context = {"event": event, "what": what, "limit": limit}
    if what == "results":
        if not event.results_published:
            context["rows"] = None
        else:
            rows = judging_services.placed(event, track_id)
            context["rows"] = [r for r in rows if r.place][:limit]
            context["scale_max"] = judging_services.ensure_rubric(event).scale_max
    else:
        qs = Project.objects.filter(
            event=event, status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True
        ).select_related("team", "track")
        if track_id:
            qs = qs.filter(track_id=track_id)
        context["projects"] = qs.order_by("-submitted_at", "id")[:limit]
    context["origin"] = request.build_absolute_uri("/").rstrip("/")
    return render(request, "events/embed.html", context)
