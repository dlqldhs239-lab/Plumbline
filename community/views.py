from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from events.models import Event, Project
from events.permissions import is_organizer
from judging import export as export_services

from . import services
from .models import Comment, Voter


def _event(slug):
    return get_object_or_404(Event.objects.prefetch_related("tracks"), slug=slug)


def ballot(request, slug, token=None):
    """The voting page. Order is random per voter and stable across reloads.
    Totals are never shown here; results appear only after publication."""
    event = _event(slug)
    if event.voting_access == Event.VotingAccess.CLOSED:
        return render(
            request, "community/ballot_closed.html", {"event": event, "reason": "This event has no community vote."}
        )
    if not event.voting_open():
        return render(
            request, "community/ballot_closed.html", {"event": event, "reason": "Voting is not open right now."}
        )
    voter = None
    response = None
    try:
        if (
            token
            or event.voting_access == Event.VotingAccess.OPEN
            or (event.voting_access == Event.VotingAccess.AUTHENTICATED and request.user.is_authenticated)
        ):
            voter = services.admit_voter(request, event, ballot_token=token)
        else:
            voter = services.voter_from_request(request, event)
    except PermissionDenied as e:
        return render(request, "community/ballot_closed.html", {"event": event, "reason": str(e)}, status=403)
    if token and voter:
        response = redirect("ballot", slug=slug)
        return services.attach_cookie(response, event, voter)
    if voter is None:
        if event.voting_access == Event.VotingAccess.AUTHENTICATED:
            return redirect(f"/accounts/login/?next={request.path}")
        return render(
            request,
            "community/ballot_closed.html",
            {"event": event, "reason": "This event needs a personal ballot link."},
            status=403,
        )
    projects = services.ballot_projects(event, voter.key)
    weights = services.voter_weights(voter)
    used = sum(w * w for w in weights.values()) if event.voting_credits else None
    response = render(
        request,
        "community/ballot.html",
        {
            "event": event,
            "voter": voter,
            "projects": projects,
            "weights": weights,
            "quadratic": bool(event.voting_credits),
            "credits": event.voting_credits,
            "used": used,
            "left": (event.voting_credits - used) if used is not None else None,
        },
    )
    return services.attach_cookie(response, event, voter)


@require_POST
def vote(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project, pk=pk, event=event)
    voter = services.voter_from_request(request, event)
    if voter is None:
        raise PermissionDenied("No ballot for this browser. Open the ballot page first.")
    try:
        services.cast_vote(request, event, voter, project, request.POST.get("weight", "1"))
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
    return redirect(f"/events/{slug}/ballot/#p{pk}")


@login_required
@require_POST
def add_comment(request, slug, pk):
    event = _event(slug)
    project = get_object_or_404(Project, pk=pk, event=event)
    try:
        services.add_comment(request, project, request.POST.get("body", ""))
        messages.success(request, "Comment posted.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, "; ".join(getattr(e, "messages", [str(e)])))
    return redirect(project.get_absolute_url() + "#comments")


@login_required
@require_POST
def hide_comment(request, slug, pk, comment_id):
    event = _event(slug)
    comment = get_object_or_404(Comment, pk=comment_id, project__pk=pk, project__event=event)
    services.hide_comment(comment, request.user, request.POST.get("hidden", "1") == "1")
    return redirect(comment.project.get_absolute_url() + "#comments")


# --- organizer -------------------------------------------------------------------


@login_required
def organize_voting(request, slug):
    event = _event(slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "issue":
            emails = [e for e in (request.POST.get("emails") or "").replace(",", "\n").splitlines()]
            created = services.create_email_voters(event, request.user, emails)
            messages.success(
                request,
                f"{len(created)} ballot link{'s' if len(created) != 1 else ''} created. Export the CSV and send them with your mail tool.",
            )
        elif action == "void":
            voter = get_object_or_404(Voter, pk=request.POST.get("voter"), event=event)
            services.void_voter(voter, request.user, request.POST.get("reason", ""))
            messages.info(request, "Ballot voided; its votes no longer count.")
        return redirect("organize_voting", slug=slug)
    report = services.integrity_report(event)
    tallies = services.tally(event)
    projects = sorted(
        (
            (p, tallies.get(p.id, {"votes": 0, "voters": 0}))
            for p in event.projects.filter(status=Project.Status.SUBMITTED, is_hidden=False)
        ),
        key=lambda pt: -pt[1]["votes"],
    )
    return render(request, "events/organize/voting.html", {"event": event, "report": report, "projects": projects})


@login_required
def export_ballot_links(request, slug):
    event = _event(slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    rows = [["email", "ballot_url", "voided"]]
    for v in Voter.objects.filter(event=event, kind=Voter.Kind.EMAIL).order_by("email"):
        rows.append(
            [v.email, request.build_absolute_uri(f"/events/{event.slug}/ballot/{v.ballot_token}/"), bool(v.voided_at)]
        )
    response = HttpResponse(export_services.to_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-ballot-links.csv"'
    return response


@login_required
def export_votes(request, slug):
    event = _event(slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    rows = [["project_id", "project", "voter_kind", "voter", "weight", "voided", "flags", "cast_at"]]
    qs = event.votes.select_related("project", "voter", "voter__user").order_by("project_id", "created_at")
    for v in qs:
        who = v.voter.email or (v.voter.user.username if v.voter.user else v.voter.key[:8])
        rows.append(
            [
                v.project_id,
                v.project.title,
                v.voter.kind,
                who,
                v.weight,
                bool(v.voter.voided_at),
                ";".join(v.voter.flags),
                v.created_at.isoformat(),
            ]
        )
    response = HttpResponse(export_services.to_csv(rows), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-votes.csv"'
    return response
