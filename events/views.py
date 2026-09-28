from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.services import record
from judging import export as export_services
from judging import services as judging_services
from judging import showcase
from judging.models import JudgeAssignment, JudgeCalibration, ProjectResult
from plumbline.inputs import as_id, id_or_404, site_url

from . import services
from .forms import AssignForm, CriterionFormSet, EventForm, JudgeInviteForm, ProjectForm, TeamForm
from .models import Event, EventRole, Project, Role, Team, TeamInvite, TeamMembership
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
            distinct=True,
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
    featured = showcase.featured_event()
    case = showcase.case(featured) if featured else None
    return render(
        request,
        "events/home.html",
        {
            "events": qs,
            "featured": featured,
            "case": case,
            "panel": showcase.panel(featured) if case else None,
        },
    )


def event_detail(request, slug):
    event = _event(slug)
    roles = roles_for(request.user, event)
    membership = None
    if request.user.is_authenticated:
        membership = TeamMembership.objects.filter(team__event=event, user=request.user).select_related("team").first()
    public = event.projects.filter(status=Project.Status.SUBMITTED, is_hidden=False, duplicate_of__isnull=True)
    per_track = dict(public.order_by().values_list("track_id").annotate(n=Count("id")))
    tracks = [{"track": t, "n": per_track.get(t.id, 0)} for t in event.tracks.all()]
    rubric = judging_services.ensure_rubric(event)
    criteria = list(rubric.criteria.all())
    total_weight = sum((c.weight for c in criteria), 0) or 1
    for c in criteria:
        c.share = round(100 * float(c.weight) / float(total_weight))
    return render(
        request,
        "events/event_detail.html",
        {
            "event": event,
            "roles": roles,
            "is_organizer": is_organizer(request.user, event),
            "membership": membership,
            "project_count": public.count(),
            "team_count": event.teams.count(),
            "judge_count": event.roles.filter(role=Role.JUDGE).count(),
            "tracks": tracks,
            "rubric": rubric,
            "criteria": criteria,
            "timeline": timeline(event),
            "now": timezone.now(),
        },
    )


def timeline(event, now=None) -> dict:
    """The event's dates on one line, and where today falls on it. Positions
    are percentages of the span from the first date to the last."""
    now = now or timezone.now()
    marks = [
        ("Submissions open", event.submissions_open_at),
        ("Submissions close", event.submissions_close_at),
        ("Judging opens", event.judging_open_at),
        ("Judging closes", event.judging_close_at),
        ("Voting closes", event.voting_close_at if event.voting_access != "closed" else None),
        ("Results", event.results_published_at),
    ]
    marks = sorted(((label, at) for label, at in marks if at), key=lambda m: m[1])
    merged = []
    for label, at in marks:
        if merged and merged[-1]["at"] == at:
            merged[-1]["label"] += " · " + label.lower()
        else:
            merged.append({"label": label, "at": at})
    start, end = merged[0]["at"], merged[-1]["at"]
    span = (end - start).total_seconds() or 1
    for m in merged:
        m["pct"] = round(100 * (m["at"] - start).total_seconds() / span, 2)
        m["past"] = m["at"] <= now
    # Labels that would sit on top of each other take turns above and below the line.
    for i, m in enumerate(merged):
        m["row"] = i % 2
    here = None
    if start <= now <= end:
        here = round(100 * (now - start).total_seconds() / span, 2)
    return {"marks": merged, "here": here, "before": now < start, "after": now > end}


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
    if track:
        track_id = as_id(track)
        qs = qs.filter(track_id=track_id) if track_id else qs.none()
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
    result, ranked, in_track = None, 0, None
    if event.results_published or is_organizer(request.user, event):
        result = next((r for r in judging_services.placed(event) if r.project_id == project.pk), None)
        if result is not None and result.place:
            ranked = result.of
            if project.track_id:
                mine = next(r for r in judging_services.placed(event, project.track_id) if r.project_id == project.pk)
                in_track = {"rank": mine.place, "of": mine.of}
    organizer = is_organizer(request.user, event)
    member = services.is_team_member(request.user, project)
    breakdown, feedback = None, []
    rubric = judging_services.ensure_rubric(event)
    if result is not None and (event.results_published or organizer):
        breakdown = result.criterion_means or judging_services.criterion_means(project)
    if (event.results_published and member) or organizer:
        feedback = judging_services.feedback_for(project)
    twins = services.lookalikes(project) if organizer else []
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
            "ranked": ranked,
            "in_track": in_track,
            "rubric": rubric,
            "comments": comments,
            "breakdown": breakdown,
            "feedback": feedback,
            "is_member": member,
            "lookalikes": twins,
        },
    )


def results(request, slug):
    event = _event(slug)
    if not event.results_published and not is_organizer(request.user, event):
        return render(request, "events/results_hidden.html", {"event": event}, status=403)
    track = request.GET.get("track") or ""
    track_id = as_id(track)
    if track and (track_id is None or not any(t.id == track_id for t in event.tracks.all())):
        rows = []  # a track that is not one of this event's shows nothing, not the untracked projects
    else:
        rows = judging_services.placed(event, track_id if track else None)
    ranked = [r for r in rows if r.rank]
    rubric = judging_services.ensure_rubric(event)
    calibration = JudgeCalibration.objects.filter(event=event)
    facts = {
        "projects": len(ranked),
        "reviews": sum(r.review_count for r in ranked),
        "judges": calibration.count(),
        "flat": calibration.filter(flat=True).count(),
        "moved": sum(1 for r in ranked if r.shift),
        "fewest": min((r.review_count for r in ranked), default=0),
        "most": max((r.review_count for r in ranked), default=0),
    }
    return render(
        request,
        "events/results.html",
        {
            "event": event,
            "rows": rows,
            "leaders": ranked[:3],
            "rest": ranked[3:] + [r for r in rows if not r.rank],
            "slope": showcase.slopegraph(ranked),
            "facts": facts,
            "rubric": rubric,
            "jury_k": judging_services.computed_jury_k(event),
            "track": track,
            "track_name": next((t.name for t in event.tracks.all() if t.id == track_id), ""),
            "voting": event.voting_access != "closed",
            "preview": not event.results_published,
        },
    )


# --- participant -------------------------------------------------------------


@login_required
def dashboard(request):
    user = request.user
    memberships = TeamMembership.objects.filter(user=user).select_related("team", "team__event")
    projects = list(
        Project.objects.filter(team__memberships__user=user).select_related("event", "team", "track").distinct()
    )
    # A team sees its own place once the organizers have published, never before.
    published = [p.id for p in projects if p.event.results_published and p.is_public and not p.duplicate_of_id]
    places = {r.project_id: r for r in ProjectResult.objects.filter(project_id__in=published, rank__isnull=False)}
    for p in projects:
        p.standing = places.get(p.id)
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
    from records.models import Record

    return render(
        request,
        "events/dashboard.html",
        {
            "memberships": memberships,
            "projects": projects,
            "judge_stats": judge_stats,
            "organized": organized,
            "records": Record.objects.filter(recipient=user, revoked_at__isnull=True).select_related("event"),
        },
    )


@login_required
def event_create(request):
    form = EventForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            event = services.create_event(request.user, form.event_data(), form.track_names())
        except ValidationError as e:
            form.add_error(None, e)
        else:
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
    for i in invites:
        i.address = site_url(request, i.get_absolute_url())
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
            team = get_object_or_404(Team, pk=id_or_404(request.POST.get("team_id")), event=event)
        try:
            # One unit: if submitting is refused, no stray draft is left behind.
            with transaction.atomic():
                project = services.create_project(event, team, request.user, form.data_dict())
                _save_answers(project, form, request.user)
                if "submit" in request.POST:
                    services.submit_project(project, request.user)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, getattr(e, "messages", [str(e)]))
        else:
            if "submit" in request.POST:
                messages.success(request, "Project submitted. You can keep editing until the deadline.")
            else:
                messages.success(request, "Draft saved.")
            return redirect(project)
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
        submitting = "submit" in request.POST and project.status != Project.Status.SUBMITTED
        try:
            with transaction.atomic():
                services.update_project(project, request.user, form.data_dict())
                _save_answers(project, form, request.user)
                if submitting:
                    services.submit_project(project, request.user)
        except (ValidationError, PermissionDenied) as e:
            form.add_error(None, getattr(e, "messages", [str(e)]))
        else:
            messages.success(request, "Project submitted." if submitting else "Saved.")
            return redirect(project)
    return render(
        request, "events/project_form.html", {"event": event, "form": form, "team": project.team, "project": project}
    )


def _save_answers(project, form, user):
    services.set_answers(project, user, form.answers())


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
    progress = judging_services.progress(event)
    return render(
        request,
        "events/organize/dashboard.html",
        {
            "event": event,
            "counts": counts,
            "progress": progress,
            "per_project": per_project,
            "attention": attention(event, counts, progress, per_project),
            "timeline": timeline(event),
            "now": timezone.now(),
        },
    )


def pairwise_summary(event, user):
    from judging import pairwise_services

    return pairwise_services.summary(event, user)


def results_state(event) -> dict:
    """Whether the stored results still describe the reviews that are in."""
    from django.db.models import Max

    computed = ProjectResult.objects.filter(event=event).aggregate(at=Max("computed_at"))["at"]
    changed = JudgeAssignment.objects.filter(event=event, status=JudgeAssignment.Status.SUBMITTED).aggregate(
        at=Max("updated_at")
    )["at"]
    eligible = set(judging_services.eligible_projects(event).values_list("id", flat=True))
    stored = set(ProjectResult.objects.filter(event=event).values_list("project_id", flat=True))
    used = judging_services.computed_jury_k(event)
    wanted = judging_services.ensure_rubric(event).effective_jury_k()
    reset = computed is not None and used is not None and abs(used - wanted) > 1e-9
    return {
        "computed_at": computed,
        "stale": bool(computed and ((changed and changed > computed) or eligible != stored or reset)),
        "setting_changed": reset,
        "used_jury_k": used,
        "never": computed is None,
    }


def attention(event, counts, progress, per_project) -> list[dict]:
    """What an organizer should look at now, most pressing first. Each entry
    says what is wrong, how much of it, and where to go."""
    from django.urls import reverse

    out = []

    def add(level, text, url, label):
        out.append({"level": level, "text": text, "url": reverse(url, args=[event.slug]), "label": label})

    closed = event.submissions_closed()
    target = event.reviews_per_project
    if closed and counts["submitted"] and not counts["judges"]:
        add("bad", "Submissions have closed and the event has no judges.", "organize_judges", "Add judges")
    if closed and counts["judges"] and counts["submitted"]:
        unassigned = sum(1 for p in per_project if p.n_assigned == 0)
        short = sum(1 for p in per_project if 0 < p.n_assigned < target)
        if unassigned:
            add(
                "bad",
                f"{unassigned} project{'s have' if unassigned != 1 else ' has'} no judge assigned.",
                "organize_assignments",
                "Assign",
            )
        if short:
            add(
                "warn",
                f"{short} project{'s are' if short != 1 else ' is'} assigned fewer than {target} "
                f"review{'s' if target != 1 else ''}.",
                "organize_assignments",
                "Assign",
            )
    idle = [r for r in progress if r["submitted"] == 0 and r["in_progress"] == 0 and r["total"]]
    if idle and event.judging_open():
        add(
            "warn",
            f"{len(idle)} judge{'s have' if len(idle) != 1 else ' has'} not started: "
            + ", ".join(r["name"] for r in idle[:4])
            + (f" and {len(idle) - 4} more" if len(idle) > 4 else "")
            + ".",
            "organize_judges",
            "Judges",
        )
    waiting = counts["assignments"] - counts["reviews_done"]
    if waiting and event.judging_open():
        add(
            "neutral",
            f"{waiting} of {counts['assignments']} reviews are still to come.",
            "organize_assignments",
            "Assignments",
        )
    if counts["duplicates"]:
        add(
            "neutral",
            f"{counts['duplicates']} submission{'s are' if counts['duplicates'] != 1 else ' is'} marked as a "
            "duplicate and left out of judging.",
            "organize_projects",
            "Projects",
        )
    state = results_state(event)
    if counts["reviews_done"] and state["never"]:
        add("neutral", "Reviews are in and no results have been computed yet.", "organize_results", "Results")
    elif state["stale"]:
        add(
            "bad" if event.results_published else "warn",
            "Reviews or projects changed after the results were computed"
            + (", and those results are published." if event.results_published else "."),
            "organize_results",
            "Recompute",
        )
    elif counts["reviews_done"] and not waiting and not event.results_published:
        add(
            "ok",
            "Every review is in and the results are computed. They are not published.",
            "organize_results",
            "Publish",
        )
    return out


@login_required
def organize_settings(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    form = EventForm(request.POST or None, instance=event)
    if request.method == "POST" and form.is_valid():
        # Validating the form has already written the new values onto `event`;
        # the service needs the stored row to see what actually changed.
        stored = Event.objects.get(pk=event.pk)
        try:
            with transaction.atomic():
                services.update_event(stored, request.user, form.event_data())
                services.add_tracks(stored, request.user, form.track_names())
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, "Event settings saved.")
            return redirect("organize_dashboard", slug=stored.slug)
    shown = Event.objects.get(pk=event.pk) if request.method == "POST" else event
    return render(request, "events/event_form.html", {"form": form, "event": shown, "creating": False})


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
    fresh_link = None
    if request.method == "POST" and request.POST.get("link"):
        role = get_object_or_404(EventRole, pk=id_or_404(request.POST["link"]), event=event, role=Role.JUDGE)
        try:
            raw = services.issue_judge_link(role, request.user)
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
            return redirect("organize_judges", slug=slug)
        # Shown in this response only. The link is a credential, so it is
        # never written to the session or anywhere else it could be read later.
        fresh_link = {"email": role.user.email, "url": site_url(request, f"/accounts/claim/{raw}/")}
        form = JudgeInviteForm(event=event)
    elif request.method == "POST" and request.POST.get("remove"):
        role = get_object_or_404(EventRole, pk=id_or_404(request.POST["remove"]), event=event, role=Role.JUDGE)
        try:
            services.remove_judge(role, request.user)
            messages.info(request, "Judge removed.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_judges", slug=slug)
    judges = (
        event.roles.filter(role=Role.JUDGE).select_related("user").prefetch_related("tracks").order_by("user__username")
    )
    rows = [
        {
            "role": r,
            "invited": services.can_issue_sign_in_link(request.user, r.user),
            "never": r.user.last_login is None,
        }
        for r in judges
    ]
    response = render(
        request,
        "events/organize/judges.html",
        {"event": event, "form": form, "rows": rows, "fresh_link": fresh_link},
    )
    if fresh_link:
        response["Cache-Control"] = "no-store"
    return response


@login_required
def organize_rubric(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    rubric = judging_services.ensure_rubric(event)
    locked = rubric.criteria.filter(scores__isnull=False).exists()
    initial = [
        {"key": c.key, "name": c.name, "weight": c.weight, "description": c.description} for c in rubric.criteria.all()
    ]
    if request.method == "POST" and "pairwise" in request.POST:
        from judging import pairwise_services

        pairwise_services.set_enabled(event, request.user, request.POST.get("pairwise") == "on")
        messages.success(request, "Saved.")
        return redirect("organize_rubric", slug=slug)
    if request.method == "POST" and "jury_k" in request.POST:
        try:
            judging_services.set_jury_k(rubric, request.user, request.POST.get("jury_k", "").strip())
            messages.success(request, "Saved. Recompute the results to apply it.")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_rubric", slug=slug)
    formset = CriterionFormSet(request.POST or None, initial=initial, prefix="c")
    if request.method == "POST" and not locked and formset.is_valid():
        rows = [
            {k: f.cleaned_data.get(k) for k in ("key", "name", "weight", "description")}
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
        if action == "publish" and not ProjectResult.objects.filter(event=event, rank__isnull=False).exists():
            messages.error(request, "There is nothing to publish yet. Compute the results first.")
            return redirect("organize_results", slug=slug)
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
    rows = list(ProjectResult.objects.filter(event=event).select_related("project", "project__track", "project__team"))
    elevation = showcase.elevation(event)
    calibration = list(JudgeCalibration.objects.filter(event=event).select_related("judge").order_by("mean"))
    datum = elevation["datum"]["value"] if elevation else None
    for c in calibration:
        c.lean = (c.mean - datum) if datum is not None and c.mean is not None else None
        # What the method takes off this judge's scores.
        c.habit = (c.shrunk_mean - datum) if datum is not None and c.shrunk_mean is not None else None
    movers = []
    for r in rows:
        if r.rank_raw and r.rank:
            r.delta = r.rank_raw - r.rank  # positive = moved up once judges and jury size are accounted for
            r.delta_abs = abs(r.delta)
            movers.append(r)
    movers = [r for r in sorted(movers, key=lambda r: r.delta_abs, reverse=True)[:8] if r.delta]
    rubric = judging_services.ensure_rubric(event)
    ranked = [r for r in rows if r.rank]
    return render(
        request,
        "events/organize/results.html",
        {
            "event": event,
            "rows": rows,
            "calibration": calibration,
            "movers": movers,
            "elevation": elevation,
            "pairwise": pairwise_summary(event, request.user),
            "state": results_state(event),
            "rubric": rubric,
            "jury_k": judging_services.computed_jury_k(event),
            "facts": {
                "projects": len(ranked),
                "reviews": sum(r.review_count for r in ranked),
                "moved": sum(1 for r in ranked if r.rank != r.rank_raw),
                "unreviewed": len(rows) - len(ranked),
            },
        },
    )


@login_required
def organize_audit(request, slug):
    event = _event(slug)
    _organizer_or_403(request, event)
    from audit import reading

    action = (request.GET.get("action") or "").strip()[:80]
    actor = (request.GET.get("actor") or "").strip()[:200]
    qs = event.audit_entries.select_related("actor").order_by("-created_at", "-id")
    if action:
        qs = qs.filter(action__startswith=action)
    if actor:
        qs = qs.filter(actor_label=actor)
    page = Paginator(qs, 60).get_page(request.GET.get("page"))
    names = event.audit_entries.values_list("action", flat=True).distinct().order_by("action")
    actors = event.audit_entries.exclude(actor_label="").values_list("actor_label", flat=True).distinct()
    return render(
        request,
        "events/organize/audit.html",
        {
            "event": event,
            "page": page,
            "days": reading.by_day(page.object_list),
            "action": action,
            "actor": actor,
            "actions": sorted(({"name": n, "says": reading.phrase(n)} for n in names), key=lambda a: a["says"]),
            "actors": sorted(actors, key=str.lower),
        },
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
