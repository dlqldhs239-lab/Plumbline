"""Prizes, form questions and bulk import over the API. The console pages
call the same service functions."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from ninja import Schema

from events import extras, importer
from events.models import CustomQuestion, Prize

from .auth import auth_optional, auth_required
from .router import _event, _require_organizer, api
from .schemas import ErrorOut


class PrizeIn(Schema):
    name: str
    description: str = ""
    amount: float | None = None
    currency: str = "USD"
    track: int | None = None
    order: int | None = None


class PrizeOut(Schema):
    id: int
    name: str
    description: str = ""
    amount: float | None = None
    currency: str
    track: str | None = None
    order: int


class QuestionIn(Schema):
    prompt: str
    help_text: str = ""
    kind: str = "text"
    choices: list[str] = []
    required: bool = False
    order: int | None = None


class QuestionOut(Schema):
    id: int
    prompt: str
    help_text: str = ""
    kind: str
    choices: list[str]
    required: bool
    order: int


class ImportIn(Schema):
    kind: str = "projects"
    csv: str
    dry_run: bool = True


class ImportRow(Schema):
    line: int
    what: str
    ok: bool
    problems: list[str]
    notes: list[str]


class ImportOut(Schema):
    kind: str
    dry_run: bool
    total: int
    bad: int
    ready: bool
    imported: dict | None = None
    ignored_columns: list[str] = []
    rows: list[ImportRow]


def prize_out(p: Prize) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "amount": float(p.amount) if p.amount is not None else None,
        "currency": p.currency,
        "track": p.track.name if p.track else None,
        "order": p.order,
    }


def question_out(q: CustomQuestion) -> dict:
    return {
        "id": q.id,
        "prompt": q.prompt,
        "help_text": q.help_text,
        "kind": q.kind,
        "choices": q.choices or [],
        "required": q.required,
        "order": q.order,
    }


@api.get("/events/{slug}/prizes", response=list[PrizeOut], auth=auth_optional, tags=["events"])
def list_prizes(request, slug: str):
    return [prize_out(p) for p in _event(slug).prizes.select_related("track")]


@api.post(
    "/events/{slug}/prizes", response={201: PrizeOut, 400: ErrorOut, 403: ErrorOut}, auth=auth_required, tags=["events"]
)
def create_prize(request, slug: str, payload: PrizeIn):
    return 201, prize_out(extras.save_prize(_event(slug), request.user, payload.dict()))


@api.put(
    "/events/{slug}/prizes/{prize_id}",
    response={200: PrizeOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["events"],
)
def update_prize(request, slug: str, prize_id: int, payload: PrizeIn):
    event = _event(slug)
    prize = get_object_or_404(Prize, pk=prize_id, event=event)
    return prize_out(extras.save_prize(event, request.user, payload.dict(), prize))


@api.delete(
    "/events/{slug}/prizes/{prize_id}", response={204: None, 403: ErrorOut}, auth=auth_required, tags=["events"]
)
def delete_prize(request, slug: str, prize_id: int):
    event = _event(slug)
    extras.delete_prize(get_object_or_404(Prize, pk=prize_id, event=event), request.user)
    return 204, None


@api.get("/events/{slug}/questions", response=list[QuestionOut], auth=auth_optional, tags=["events"])
def list_questions(request, slug: str):
    """The organizer's own questions on the submission form. Public: a team
    should know what it will be asked."""
    return [question_out(q) for q in _event(slug).custom_questions.all()]


@api.post(
    "/events/{slug}/questions",
    response={201: QuestionOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["events"],
)
def create_question(request, slug: str, payload: QuestionIn):
    return 201, question_out(extras.save_question(_event(slug), request.user, payload.dict()))


@api.put(
    "/events/{slug}/questions/{question_id}",
    response={200: QuestionOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["events"],
)
def update_question(request, slug: str, question_id: int, payload: QuestionIn):
    event = _event(slug)
    q = get_object_or_404(CustomQuestion, pk=question_id, event=event)
    return question_out(extras.save_question(event, request.user, payload.dict(), q))


@api.delete(
    "/events/{slug}/questions/{question_id}",
    response={204: None, 403: ErrorOut},
    auth=auth_required,
    tags=["events"],
)
def delete_question(request, slug: str, question_id: int):
    event = _event(slug)
    extras.delete_question(get_object_or_404(CustomQuestion, pk=question_id, event=event), request.user)
    return 204, None


@api.post(
    "/events/{slug}/import",
    response={200: ImportOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["events"],
)
def import_csv(request, slug: str, payload: ImportIn):
    """Import projects (with teams and members) or judges from CSV text.
    `dry_run` is true unless you say otherwise: the answer then says what
    would happen, row by row, and nothing is written."""
    event = _event(slug)
    _require_organizer(request, event)
    checked = importer.plan(event, request.user, payload.csv, payload.kind)
    made = None
    if not payload.dry_run:
        made = importer.apply(event, request.user, payload.csv, payload.kind)
    return {
        "kind": checked["kind"],
        "dry_run": payload.dry_run,
        "total": checked["total"],
        "bad": checked["bad"],
        "ready": checked["ready"],
        "imported": made,
        "ignored_columns": checked["ignored_columns"],
        "rows": [
            {"line": r["line"], "what": r["what"], "ok": r["ok"], "problems": r["problems"], "notes": r["notes"]}
            for r in checked["rows"]
        ],
    }
