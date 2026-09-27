from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.services import record
from judging import export as export_services
from judging import services as judging_services
from judging.models import JudgeAssignment, JudgeCalibration, ProjectResult

from . import services
from .forms import AssignForm, CriterionFormSet, EventForm, JudgeInviteForm, ProjectForm, TeamForm
from .models import CustomAnswer, Event, EventRole, Project, Role, Team, TeamInvite, TeamMembership
from .permissions import can_edit_project, can_view_project, is_admin, is_organizer, roles_for

GALLERY_PAGE_SIZE = 60


def _event(slug):
    return get_object_or_404(Event.objects.prefetch_related("tracks", "prizes"), slug=slug)


def _organizer_or_403(request, event):
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")


# --- public ------------------------------------------------------------------


def home(request):
    qs = Event.objects.annotate(
        project_count=Count(
            "projects",
            filter=Q(
                projects__status=Project.Status.SUBMITTED,
                projects__is_hidden=False,
                projects__duplicate_of__isnull=True,
            ),
        )
    )
    if not is_admin(request.user):
        visible = Q(is_listed=True)
        if request.user.is_authenticated:
            visible |= Q(roles__user=request.user)
        qs = qs.filter(visible).distinct()
    return render(request, "events/home.html", {"events": qs})


def event_detail(request, slug):
    event = _event(slug)
    roles = roles_for(request.user, event)
    membership = None
    if request.user.is_authenticated:
        membership = TeamMembership.objects.filter(team__event=event, user=request.user).select_related("team").first()
    project_count = event.projects.filter(
        status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True
    ).count()
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "roles": roles,
            "is_organizer": is_organizer(request.user, event),
            "membership": membership,
            "project_count": project_count,
            "now": timezone.now(),
        },
    )


def gallery(request, slug):
    """Public gallery. Stable ordering (newest submission first, then id) so
    page one is deterministic; randomisation belongs on ballots, not here."""
    event = _event(slug)
    q = (request.GET.get("q") or "").strip()
    track = request.GET.get("track") or ""
    qs = Project.objects.filter(
        event=event, status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True
    ).select_related("team", "track")
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(tagline__icontains=q) | Q(team__name__icontains=q) | Q(tech_tags__icontains=q)
        )
    if track.isdigit():
        qs = qs.filter(track_id=int(track))
    qs = qs.order_by("-submitted_at", "id")
    page = Paginator(qs, GALLERY_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "events/gallery.html", {"event": event, "page": page, "q": q, "track": track})


def project_detail(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track", "event"), pk=pk, event=event)
    if not can_view_project(request.user, project):
        raise PermissionDenied("This project is not public.")
    answers = project.answers.select_related("question").order_by("question__order")
    members = project.team.memberships.select_related("user")
    result = None
    if event.results_published:
        result = ProjectResult.objects.filter(project=project).first()
    organizer = is_organizer(request.user, event)
    comments = project.comments.select_related("author")
    if not organizer:
        comments = comments.filter(hidden_at__isnull=True)
    return render(
        request,
        "events/project_detail.html",
        {
            "event": event,
            "project": project,
            "answers": answers,
            "members": members,
            "can_edit": can_edit_project(request.user, project),
            "editing_open": event.submissions_open() or organizer,
            "is_organizer": organizer,
            "result": result,
            "comments": comments,
        },
    )


def results(request, slug):
    event = _event(slug)
    if not event.results_published and not is_organizer(request.user, event):
        return render(request, "events/results_hidden.html", {"event": event}, status=403)
    rows = ProjectResult.objects.filter(event=event).select_related("project", "project__track", "project__team")
    return render(
        request, "events/results.html", {"event": event, "rows": rows, "preview": not event.results_published}
    )


# --- participant -------------------------------------------------------------


@login_required
def dashboard(request):
    user = request.user
    memberships = TeamMembership.objects.filter(user=user).select_related("team", "team__event")
    projects = Project.objects.filter(team__memberships__user=user).select_related("event", "team", "track").distinct()
    judge_events = Event.objects.filter(roles__user=user, roles__role=Role.JUDGE).distinct()
    judge_stats = []
    for ev in judge_events:
        qs = judging_services.assignments_for_judge(user, ev)
        judge_stats.append(
            {"event": ev, "total": qs.count(), "done": qs.filter(status=JudgeAssignment.Status.SUBMITTED).count()}
        )
    organized = Event.objects.filter(roles__user=user, roles__role=Role.ORGANIZER).distinct()
    if is_admin(user):
        organized = Event.objects.all()
    return render(
        request,
        "events/dashboard.html",
        {"memberships": memberships, "projects": projects, "judge_stats": judge_stats, "organized": organized},
    )


@login_required
def event_create(request):
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.created_by = request.user
        event.save()
        form.save()  # tracks
        EventRole.objects.get_or_create(event=event, user=request.user, role=Role.ORGANIZER)
        judging_services.ensure_rubric(event)
        record("event.create", event=event, target=event)
        messages.success(request, f"{event.name} created. You are its organizer.")
        return redirect("organize_dashboard", slug=event.slug)
    return render(request, "events/event_form.html", {"form": form, "creating": True})


@login_required
def team_create(request, slug):
    event = _event(slug)
    form = TeamForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            team = services.create_team(event, request.user, form.cleaned_data["name"])
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, f"Team {team.name} created. Share an invite link to add teammates.")
            return redirect("team_detail", slug=slug, pk=team.pk)
    return render(request, "events/team_form.html", {"event": event, "form": form})


@login_required
def team_detail(request, slug, pk):
    event = _event(slug)
    team = get_object_or_404(Team, pk=pk, event=event)
    member = TeamMembership.objects.filter(team=team, user=request.user).exists()
    if not member and not is_organizer(request.user, event):
        raise PermissionDenied("You are not on this team.")
    invites = [i for i in team.invites.all() if i.is_valid()]
    return render(
        request,
        "events/team_detail.html",
        {
            "event": event,
            "team": team,
            "members": team.memberships.select_related("user"),
            "invites": invites,
            "projects": team.projects.all(),
        },
    )


@login_required
@require_POST
def team_invite(request, slug, pk):
    event = _event(slug)
    team = get_object_or_404(Team, pk=pk, event=event)
    services.create_invite(team, request.user)
    messages.success(request, "Invite link created. It works for 72 hours or 10 joins.")
    return redirect("team_detail", slug=slug, pk=pk)


@login_required
def team_join(request, token):
    invite = get_object_or_404(TeamInvite.objects.select_related("team", "team__event"), token=token)
    team = invite.team
    if request.method == "POST":
        try:
            services.join_team(invite, request.user)
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
            return redirect("event_detail", slug=team.event.slug)
        messages.success(request, f"You joined {team.name}.")
        return redirect("team_detail", slug=team.event.slug, pk=team.pk)
    return render(
        request,
        "events/team_join.html",
        {"invite": invite, "team": team, "event": team.event, "valid": invite.is_valid()},
    )


@login_required
def project_create(request, slug):
    event = _event(slug)
    membership = TeamMembership.objects.filter(team__event=event, user=request.user).select_related("team").first()
    if membership is None and not is_organizer(request.user, event):
        messages.info(request, "Create or join a team before submitting a project.")
        return redirect("team_create", slug=slug)
    team = membership.team if membership else None
    form = ProjectForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        if team is None:
            team = get_object_or_404(Team, pk=request.POST.get("team_id"), event=event)
        try:
            project = services.create_project(event, team, request.user, form.data_dict())
            _save_answers(project, form)
            if "submit" in request.POST:
                services.submit_project(project, request.user)
                messages.success(request, "Project submitted. You can keep editing until the deadline.")
            else:
                messages.success(request, "Draft saved.")
            return redirect(project)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, getattr(e, "messages", [str(e)]))
    return render(
        request,
        "events/project_form.html",
        {
            "event": event,
            "form": form,
            "team": team,
            "project": None,
            "teams": event.teams.all() if team is None else None,
        },
    )


@login_required
def project_edit(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project.objects.select_related("team", "track"), pk=pk, event=event)
    if not can_edit_project(request.user, project):
        raise PermissionDenied("You are not on this project's team.")
    form = ProjectForm(request.POST or None, event=event, instance=project)
    if request.method == "POST" and form.is_valid():
        try:
            services.update_project(project, request.user, form.data_dict())
            _save_answers(project, form)
            if "submit" in request.POST and project.status != Project.Status.SUBMITTED:
                services.submit_project(project, request.user)
                messages.success(request, "Project submitted.")
            else:
                messages.success(request, "Saved.")
            return redirect(project)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, getattr(e, "messages", [str(e)]))
    return render(
        request, "events/project_form.html", {"event": event, "form": form, "team": project.team, "project": project}
    )


def _save_answers(project, form):
    for qid, value in form.answers().items():
        CustomAnswer.objects.update_or_create(project=project, question_id=qid, defaults={"value": value})


@login_required
@require_POST
def project_submit(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project, pk=pk, event=event)
    try:
        services.submit_project(project, request.user)
        messages.success(request, "Project submitted.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
    return redirect(project)


@login_required
@require_POST
def project_withdraw(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project, pk=pk, event=event)
    try:
        services.withdraw_project(project, request.user)
        messages.info(request, "Project withdrawn. You can resubmit before the deadline.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
    return redirect(project)


# --- organizer ---------------------------------------------------------------


@login_required
def organize_dashboard(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    counts = {
        "submitted": event.projects.filter(
            status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True
        ).count(),
        "drafts": event.projects.filter(status=Project.Status.DRAFT).count(),
        "hidden": event.projects.filter(is_hidden=True).count(),
        "duplicates": event.projects.filter(duplicate_of__isnull=False).count(),
        "teams": event.teams.count(),
        "judges": event.roles.filter(role=Role.JUDGE).count(),
        "assignments": event.assignments.count(),
        "reviews_done": event.assignments.filter(status=JudgeAssignment.Status.SUBMITTED).count(),
    }
    per_project = (
        judging_services.eligible_projects(event)
        .annotate(
            n_assigned=Count("assignments"),
            n_done=Count("assignments", filter=Q(assignments__status=JudgeAssignment.Status.SUBMITTED)),
        )
        .order_by("n_done", "n_assigned", "title")
    )
    return render(
        request,
        "events/organize/dashboard.html",
        {
            "event": event,
            "counts": counts,
            "progress": judging_services.progress(event),
            "per_project": per_project,
            "now": timezone.now(),
        },
    )


@login_required
def organize_settings(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    form = EventForm(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid():
        before = {"submissions_close_at": event.submissions_close_at.isoformat()}
        form.save()
        record(
            "event.update",
            event=event,
            target=event,
            detail={"before": before, "after": {"submissions_close_at": event.submissions_close_at.isoformat()}},
        )
        messages.success(request, "Event settings saved.")
        return redirect("organize_dashboard", slug=event.slug)
    return render(request, "events/event_form.html", {"form": form, "event": event, "creating": False})


@login_required
def organize_projects(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    projects = event.projects.select_related("team", "track", "duplicate_of").order_by("-submitted_at", "id")
    return render(request, "events/organize/projects.html", {"event": event, "projects": projects})


@login_required
@require_POST
def organize_project_hide(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project, pk=pk, event=event)
    services.set_hidden(project, request.user, request.POST.get("hidden") == "1")
    return redirect("organize_projects", slug=slug)


@login_required
def organize_judges(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    form = JudgeInviteForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        try:
            services.add_judge(
                event,
                request.user,
                form.cleaned_data["email"],
                form.cleaned_data.get("name") or "",
                list(form.cleaned_data["tracks"]),
            )
            messages.success(request, f"{form.cleaned_data['email'].lower()} is a judge.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_judges", slug=slug)
    if request.method == "POST" and request.POST.get("remove"):
        role = get_object_or_404(EventRole, pk=request.POST["remove"], event=event, role=Role.JUDGE)
        try:
            services.remove_judge(role, request.user)
            messages.info(request, "Judge removed.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_judges", slug=slug)
    judges = (
        event.roles.filter(role=Role.JUDGE).select_related("user").prefetch_related("tracks").order_by("user__username")
    )
    return render(request, "events/organize/judges.html", {"event": event, "form": form, "judges": judges})


@login_required
def organize_rubric(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    rubric = judging_services.ensure_rubric(event)
    locked = rubric.criteria.filter(scores__isnull=False).exists()
    initial = [
        {"key": c.key, "name": c.name, "weight": c.weight, "description": c.description} for c in rubric.criteria.all()
    ]
    formset = CriterionFormSet(request.POST or None, initial=initial, prefix="c")
    if request.method == "POST" and not locked and formset.is_valid():
        rows = [
            f.cleaned_data
            for f in formset
            if f.cleaned_data and not f.cleaned_data.get("DELETE") and f.cleaned_data.get("key")
        ]
        try:
            judging_services.replace_criteria(rubric, request.user, rows)
            messages.success(request, "Rubric saved.")
            return redirect("organize_rubric", slug=slug)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
    return render(
        request, "events/organize/rubric.html", {"event": event, "rubric": rubric, "formset": formset, "locked": locked}
    )


@login_required
def organize_assignments(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    form = AssignForm(request.POST or None, initial={"reviews_per_project": event.reviews_per_project})
    if request.method == "POST" and form.is_valid():
        try:
            out = judging_services.assign_balanced(
                event,
                request.user,
                form.cleaned_data["reviews_per_project"],
                form.cleaned_data.get("batch") or None,
                form.cleaned_data.get("seed"),
            )
            messages.success(
                request,
                f"Created {out['created']} assignments in {out['batch']}."
                + (
                    f" {len(out['short_projects'])} projects could not be fully covered."
                    if out["short_projects"]
                    else ""
                ),
            )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_assignments", slug=slug)
    assignments = event.assignments.select_related("judge", "project", "project__track").order_by(
        "batch", "judge__username", "project__title"
    )
    return render(
        request, "events/organize/assignments.html", {"event": event, "form": form, "assignments": assignments}
    )


@login_required
@require_POST
def organize_assignment_remove(request, slug, pk):
    event = _event(slug)
    a = get_object_or_404(JudgeAssignment, pk=pk, event=event)
    try:
        judging_services.unassign(a, request.user)
        messages.info(request, "Assignment removed.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("organize_assignments", slug=slug)


@login_required
def organize_results(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "recompute":
            out = judging_services.recompute_results(event, request.user)
            messages.success(
                request,
                f"Recomputed from {out['reviews']} reviews across {out['projects']} projects ({out['method']}).",
            )
        elif action == "publish":
            judging_services.publish_results(event, request.user, True)
            messages.success(request, "Results published.")
        elif action == "unpublish":
            judging_services.publish_results(event, request.user, False)
            messages.info(request, "Results hidden again.")
        return redirect("organize_results", slug=slug)
    rows = ProjectResult.objects.filter(event=event).select_related("project", "project__track", "project__team")
    calibration = (
        JudgeCalibration.objects.filter(event=event).select_related("judge").order_by("-flat", "judge__username")
    )
    movers = []
    for r in rows:
        if r.rank_raw and r.rank_normalized:
            r.delta = r.rank_raw - r.rank_normalized  # positive = moved up after normalization
            r.delta_abs = abs(r.delta)
            movers.append(r)
    movers = sorted(movers, key=lambda r: r.delta_abs, reverse=True)[:8]
    return render(
        request,
        "events/organize/results.html",
        {"event": event, "rows": rows, "calibration": calibration, "movers": movers},
    )


@login_required
def organize_audit(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    action = (request.GET.get("action") or "").strip()
    qs = event.audit_entries.select_related("actor")
    if action:
        qs = qs.filter(action__startswith=action)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    actions = event.audit_entries.values_list("action", flat=True).distinct().order_by("action")
    return render(
        request, "events/organize/audit.html", {"event": event, "page": page, "action": action, "actions": actions}
    )


@login_required
def organize_export(request, slug, kind):
    event = _event(slug)
    _organizer_or_403(request, event)
    builder = export_services.EXPORTS.get(kind)
    if builder is None:
        raise PermissionDenied("Unknown export.")
    filename, rows = builder(event)
    record("export.csv", event=event, detail={"kind": kind, "rows": len(rows) - 1})
    response = HttpResponse(export_services.to_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def styleguide(request):
    """The design system on one page: tokens, type, controls, tables, states.
    Public, because a fork should be able to see what it is inheriting."""
    from . import theme

    presets = []
    for key, (label, ground, ink, accent, signal) in theme.PRESETS.items():
        presets.append(
            {
                "key": key,
                "label": label,
                "colours": [ground, ink, accent, signal],
                "mode": theme.mode_of(ground),
                "ink": round(theme.contrast(ink, ground), 1),
                "accent": round(theme.contrast(accent, ground), 1),
                "signal": round(theme.contrast(signal, ground), 1),
                "problems": theme.check(ground, ink, accent, signal),
            }
        )
    event = None
    chosen = request.GET.get("theme")
    if chosen in theme.PRESETS:
        event = Event(name="Preview", slug="preview", **theme.preset(chosen))
    return render(
        request, "events/styleguide.html", {"presets": presets, "event": event, "chosen": chosen or theme.DEFAULT}
    )
