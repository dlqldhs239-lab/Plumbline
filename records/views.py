import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from events.models import Event
from events.permissions import is_organizer
from plumbline.inputs import as_int

from . import services
from .models import Record


def _find(serial: str) -> Record:
    return get_object_or_404(Record.objects.select_related("event", "project"), serial=serial.strip().upper()[:20])


def record_detail(request, serial):
    """The certificate. Public to whoever has the number, which is how a
    holder shows it to someone else."""
    rec = _find(serial)
    state = services.state_of(rec)
    return render(
        request,
        "records/certificate.html",
        {
            "rec": rec,
            "p": rec.payload,
            "event": rec.event,
            "state": state,
            "address": request.build_absolute_uri(rec.get_absolute_url()),
        },
    )


def record_json(request, serial):
    rec = _find(serial)
    response = JsonResponse(services.document(rec), json_dumps_params={"indent": 2, "ensure_ascii": False})
    response["Content-Disposition"] = f'attachment; filename="{rec.serial}.json"'
    return response


def verify(request):
    """Check a record by its number, or a whole document by its signature."""
    result, entered = None, ""
    if request.method == "POST":
        entered = (request.POST.get("document") or "").strip()[:20000]
        if entered.startswith("{"):
            try:
                doc = json.loads(entered)
                result = services.check(doc.get("payload"), doc.get("signature"))
            except (ValueError, AttributeError):
                result = {"state": "unknown", "says": "That is not a readable document."}
        elif entered:
            rec = Record.objects.filter(serial=entered.upper()[:20]).first()
            result = (
                services.state_of(rec)
                if rec
                else {"state": "unknown", "says": "No record with that number was issued here."}
            )
    return render(request, "records/verify.html", {"result": result, "entered": entered})


@login_required
def organize_records(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    if request.method == "POST":
        try:
            if request.POST.get("action") == "issue":
                places = as_int(request.POST.get("places", "3"), "Placements", 0, 100)
                out = services.issue_for_event(event, request.user, places)
                messages.success(
                    request,
                    f"{out['issued']} record{'s' if out['issued'] != 1 else ''} issued for {out['teams']} "
                    f"teams and {out['judges']} judges. {out['standing']} already stood.",
                )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_records", slug=slug)
    records = list(event.records.select_related("recipient", "project").order_by("kind", "revoked_at", "id"))
    standing = [r for r in records if not r.is_revoked]
    return render(
        request,
        "events/organize/records.html",
        {
            "event": event,
            "records": records,
            "counts": {
                "standing": len(standing),
                "placement": sum(1 for r in standing if r.kind == Record.Kind.PLACEMENT),
                "participation": sum(1 for r in standing if r.kind == Record.Kind.PARTICIPATION),
                "judge": sum(1 for r in standing if r.kind == Record.Kind.JUDGE),
                "withdrawn": len(records) - len(standing),
            },
        },
    )


@login_required
@require_POST
def organize_record_revoke(request, slug, serial):
    event = get_object_or_404(Event, slug=slug)
    rec = get_object_or_404(Record, serial=serial, event=event)
    try:
        services.revoke(rec, request.user, request.POST.get("reason", ""))
        messages.info(request, f"{rec.serial} withdrawn. Anyone who checks it is told so.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("organize_records", slug=slug)
