"""Prizes and the organizer's own questions on the submission form.

Both used to be editable only in the Django admin. They are part of setting
up an event, so they live in the console, go through the same role check as
everything else, and are audited.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.services import record
from plumbline.inputs import as_id, as_int

from .models import CustomQuestion, Event, Prize, Track
from .permissions import is_organizer

MAX_PRIZES = 50
MAX_QUESTIONS = 20
MAX_CHOICES = 30


def _organizer(user, event: Event):
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can change this.")


def _text(value, label: str, limit: int, required: bool = False) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise ValidationError(f"{label} is required.")
    if len(text) > limit:
        raise ValidationError(f"{label} can be at most {limit} characters.")
    return text


def _track(event: Event, value) -> Track | None:
    if value in (None, "", 0, "0"):
        return None
    pk = as_id(value)
    track = Track.objects.filter(event=event, pk=pk).first() if pk else None
    if track is None:
        raise ValidationError("That track does not belong to this event.")
    return track


def _amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("The amount is not a number.") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("9999999999.99"):
        raise ValidationError("The amount must be between 0 and 9,999,999,999.99.")
    return amount.quantize(Decimal("0.01"))


# --- prizes -------------------------------------------------------------------


@transaction.atomic
def save_prize(event: Event, user, data: dict, prize: Prize | None = None) -> Prize:
    _organizer(user, event)
    if prize is not None and prize.event_id != event.id:
        raise ValidationError("That prize belongs to a different event.")
    if prize is None and event.prizes.count() >= MAX_PRIZES:
        raise ValidationError(f"An event can have at most {MAX_PRIZES} prizes.")
    creating = prize is None
    prize = prize or Prize(event=event, order=event.prizes.count())
    prize.name = _text(data.get("name"), "The prize's name", 120, required=True)
    prize.description = _text(data.get("description"), "The description", 2000)
    prize.amount = _amount(data.get("amount"))
    currency = _text(data.get("currency") or "USD", "The currency", 3).upper()
    if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
        raise ValidationError("The currency is three letters, such as USD or EUR.")
    prize.currency = currency
    prize.track = _track(event, data.get("track"))
    if data.get("order") not in (None, ""):
        prize.order = as_int(data.get("order"), "The order", 0, 1000)
    prize.save()
    record(
        "prize.create" if creating else "prize.update",
        actor=user,
        event=event,
        target=prize,
        detail={"name": prize.name, "amount": prize.amount, "currency": prize.currency},
    )
    return prize


@transaction.atomic
def delete_prize(prize: Prize, user):
    _organizer(user, prize.event)
    record("prize.delete", actor=user, event=prize.event, target=prize, detail={"name": prize.name})
    prize.delete()


# --- questions ----------------------------------------------------------------


def _choices(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace("\r", "").split("\n")
    out = []
    for item in value or []:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    if len(out) > MAX_CHOICES or any(len(c) > 120 for c in out):
        raise ValidationError(f"At most {MAX_CHOICES} choices, each at most 120 characters.")
    return out


@transaction.atomic
def save_question(event: Event, user, data: dict, question: CustomQuestion | None = None) -> CustomQuestion:
    _organizer(user, event)
    if question is not None and question.event_id != event.id:
        raise ValidationError("That question belongs to a different event.")
    if question is None and event.custom_questions.count() >= MAX_QUESTIONS:
        raise ValidationError(f"A submission form can have at most {MAX_QUESTIONS} questions of your own.")
    creating = question is None
    question = question or CustomQuestion(event=event, order=event.custom_questions.count())
    kind = str(data.get("kind") or CustomQuestion.Kind.TEXT)
    if kind not in CustomQuestion.Kind.values:
        raise ValidationError("That is not a kind of question.")
    if not creating and kind != question.kind and question.answers.exclude(value="").exists():
        raise ValidationError("Teams have answered this question. Its kind can no longer change; add a new question.")
    question.kind = kind
    question.prompt = _text(data.get("prompt"), "The question", 300, required=True)
    question.help_text = _text(data.get("help_text"), "The hint", 300)
    question.required = bool(data.get("required")) and kind != CustomQuestion.Kind.CHECKBOX
    question.choices = _choices(data.get("choices")) if kind == CustomQuestion.Kind.CHOICE else []
    if kind == CustomQuestion.Kind.CHOICE and len(question.choices) < 2:
        raise ValidationError("A choice question needs at least two choices, one per line.")
    if data.get("order") not in (None, ""):
        question.order = as_int(data.get("order"), "The order", 0, 1000)
    question.save()
    record(
        "question.create" if creating else "question.update",
        actor=user,
        event=event,
        target=question,
        detail={"prompt": question.prompt, "kind": question.kind, "required": question.required},
    )
    return question


@transaction.atomic
def delete_question(question: CustomQuestion, user):
    """Removes the question and the answers teams gave to it. The audit entry
    keeps how many there were."""
    _organizer(user, question.event)
    answered = question.answers.exclude(value="").count()
    record(
        "question.delete",
        actor=user,
        event=question.event,
        target=question,
        detail={"prompt": question.prompt, "answers_removed": answered},
    )
    question.delete()
