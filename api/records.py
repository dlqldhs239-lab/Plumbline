"""Signed records over the API: issue, list, read, check."""

from __future__ import annotations

from datetime import datetime

from django.shortcuts import get_object_or_404
from ninja import Schema

from records import services
from records.models import Record

from .auth import auth_optional, auth_required
from .router import _event, _require_organizer, api
from .schemas import ErrorOut


class RecordOut(Schema):
    serial: str
    kind: str
    state: str
    issued_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str = ""
    url: str
    payload: dict
    signature: str


class IssueIn(Schema):
    places: int = 3


class IssueOut(Schema):
    issued: int
    standing: int
    teams: int
    judges: int


class CheckIn(Schema):
    payload: dict
    signature: str


class CheckOut(Schema):
    state: str
    says: str
    serial: str | None = None


class RevokeIn(Schema):
    reason: str


def record_out(request, rec: Record) -> dict:
    return {
        "serial": rec.serial,
        "kind": rec.kind,
        "state": services.state_of(rec)["state"],
        "issued_at": rec.issued_at,
        "revoked_at": rec.revoked_at,
        "revoke_reason": rec.revoke_reason,
        "url": request.build_absolute_uri(rec.get_absolute_url()),
        "payload": rec.payload,
        "signature": rec.signature,
    }


@api.post(
    "/events/{slug}/records/issue",
    response={200: IssueOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["records"],
)
def issue_records(request, slug: str, payload: IssueIn):
    """Issue every record the event owes. Needs published results. Running it
    again issues only what is missing."""
    return services.issue_for_event(_event(slug), request.user, payload.places)


@api.get("/events/{slug}/records", response=list[RecordOut], auth=auth_required, tags=["records"])
def list_records(request, slug: str):
    event = _event(slug)
    _require_organizer(request, event)
    return [record_out(request, r) for r in event.records.all()]


@api.get("/records/mine", response=list[RecordOut], auth=auth_required, tags=["records"])
def my_records(request):
    return [record_out(request, r) for r in Record.objects.filter(recipient=request.user).select_related("event")]


@api.post("/records/check", response=CheckOut, auth=auth_optional, tags=["records"])
def check_record(request, payload: CheckIn):
    """Anyone may ask. Answers genuine, revoked, altered or unknown."""
    out = services.check(payload.payload, payload.signature)
    rec = out.get("record")
    return {"state": out["state"], "says": out["says"], "serial": rec.serial if rec else None}


@api.get("/records/{serial}", response={200: RecordOut, 404: ErrorOut}, auth=auth_optional, tags=["records"])
def get_record(request, serial: str):
    """Public to whoever has the number, like the certificate page."""
    return record_out(request, get_object_or_404(Record, serial=serial.strip().upper()[:20]))


@api.post(
    "/events/{slug}/records/{serial}/revoke",
    response={200: RecordOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["records"],
)
def revoke_record(request, slug: str, serial: str, payload: RevokeIn):
    event = _event(slug)
    rec = get_object_or_404(Record, serial=serial, event=event)
    return record_out(request, services.revoke(rec, request.user, payload.reason))
