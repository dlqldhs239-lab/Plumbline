import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from events.models import Event
from events.permissions import is_organizer
from plumbline.inputs import as_int, site_url

from . import services
from .models import Record


def _no_repeats(pairs):
    """A key written twice in JSON keeps its last value when read, so a
    document could show one place to the eye and carry another to the check.
    Such a document is refused."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("a key is repeated")
        out[key] = value
    return out


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
            # While a record does not rest on the published results, withdrawn
            # or not, what it says is not shown either: it may name a place in
            # results that are not public.
            "withheld": state["withheld"],
            "address": site_url(request, rec.get_absolute_url()),
        },
    )


def record_json(request, serial):
    rec = _find(serial)
    if services.state_of(rec)["withheld"]:
        raise PermissionDenied("This record does not stand at present.")
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
                doc = json.loads(entered, object_pairs_hook=_no_repeats)
                result = services.check(doc.get("payload"), doc.get("signature"))
            except (ValueError, AttributeError, RecursionError):
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
                    f"teams and {out['judges']} judges. {out['standing']} already stood."
                    + (f" {out['withdrawn']} withdrawn: no longer in the results." if out["withdrawn"] else "")
                    + (
                        f" {out['held_back']} not issued again, because you withdrew the same statement earlier."
                        if out["held_back"]
                        else ""
                    ),
                )
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_records", slug=slug)
    records = list(event.records.select_related("recipient", "project", "event").order_by("id"))
    # Places first, in order; then participation; then the judges.
    first = {Record.Kind.PLACEMENT: 0, Record.Kind.PARTICIPATION: 1, Record.Kind.JUDGE: 2}
    records.sort(key=lambda r: (r.is_revoked, first.get(r.kind, 3), r.payload.get("place") or 0, r.id))
    ground = services.Ground(event)
    for r in records:
        r.resting = "" if r.is_revoked else services.why_not_standing(r, ground)
    standing = [r for r in records if not r.is_revoked and not r.resting]
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
    rec = get_object_or_404(Record, serial=serial.strip().upper()[:20], event=event)
    try:
        services.revoke(rec, request.user, request.POST.get("reason", ""))
        messages.info(request, f"{rec.serial} withdrawn. Anyone who checks it is told so.")
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
    return redirect("organize_records", slug=slug)
