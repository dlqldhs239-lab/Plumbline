"""Webhook management endpoints (organizers only)."""

from __future__ import annotations

from datetime import datetime

from django.shortcuts import get_object_or_404
from ninja import Schema

from integrations import services
from integrations.models import Webhook, WebhookDelivery

from .auth import auth_required
from .router import _event, _require_organizer, api
from .schemas import ErrorOut


class WebhookIn(Schema):
    url: str
    actions: list[str] = []
    description: str = ""


class WebhookOut(Schema):
    id: int
    url: str
    actions: list[str]
    description: str = ""
    active: bool
    secret: str
    created_at: datetime


class DeliveryOut(Schema):
    id: int
    webhook_id: int
    action: str
    status: str
    status_code: int | None = None
    attempts: int
    error: str = ""
    created_at: datetime
    last_attempt_at: datetime | None = None


def webhook_out(h: Webhook) -> dict:
    return {
        "id": h.id,
        "url": h.url,
        "actions": h.actions,
        "description": h.description,
        "active": h.active,
        "secret": h.secret,
        "created_at": h.created_at,
    }


def delivery_out(d: WebhookDelivery) -> dict:
    return {
        "id": d.id,
        "webhook_id": d.webhook_id,
        "action": d.action,
        "status": d.status,
        "status_code": d.status_code,
        "attempts": d.attempts,
        "error": d.error,
        "created_at": d.created_at,
        "last_attempt_at": d.last_attempt_at,
    }


@api.get("/events/{slug}/webhooks", response=list[WebhookOut], auth=auth_required, tags=["webhooks"])
def list_webhooks(request, slug: str):
    event = _event(slug)
    _require_organizer(request, event)
    return [webhook_out(h) for h in event.webhooks.all()]


@api.post(
    "/events/{slug}/webhooks",
    response={201: WebhookOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["webhooks"],
)
def create_webhook(request, slug: str, payload: WebhookIn):
    """Subscribe a URL to this event's audit stream. `actions` are prefixes
    (`project.`, `score.submit`); empty means everything."""
    event = _event(slug)
    hook = services.create_webhook(event, request.user, payload.url, payload.actions, payload.description)
    return 201, webhook_out(hook)


@api.delete(
    "/events/{slug}/webhooks/{webhook_id}", response={204: None, 403: ErrorOut}, auth=auth_required, tags=["webhooks"]
)
def delete_webhook(request, slug: str, webhook_id: int):
    event = _event(slug)
    hook = get_object_or_404(Webhook, pk=webhook_id, event=event)
    services.delete_webhook(hook, request.user)
    return 204, None


@api.post(
    "/events/{slug}/webhooks/{webhook_id}/active",
    response={200: WebhookOut, 403: ErrorOut},
    auth=auth_required,
    tags=["webhooks"],
)
def set_webhook_active(request, slug: str, webhook_id: int, active: bool = True):
    event = _event(slug)
    hook = get_object_or_404(Webhook, pk=webhook_id, event=event)
    return webhook_out(services.set_active(hook, request.user, active))


@api.post(
    "/events/{slug}/webhooks/{webhook_id}/ping",
    response={200: DeliveryOut, 403: ErrorOut},
    auth=auth_required,
    tags=["webhooks"],
)
def ping_webhook(request, slug: str, webhook_id: int):
    event = _event(slug)
    hook = get_object_or_404(Webhook, pk=webhook_id, event=event)
    return delivery_out(services.send_test(hook, request.user))


@api.get("/events/{slug}/webhook-deliveries", response=list[DeliveryOut], auth=auth_required, tags=["webhooks"])
def list_deliveries(request, slug: str, status: str = "", limit: int = 100):
    event = _event(slug)
    _require_organizer(request, event)
    qs = WebhookDelivery.objects.filter(webhook__event=event)
    if status:
        qs = qs.filter(status=status)
    return [delivery_out(d) for d in qs[: max(1, min(limit, 500))]]


@api.post(
    "/events/{slug}/webhook-deliveries/{delivery_id}/retry",
    response={200: DeliveryOut, 400: ErrorOut, 403: ErrorOut},
    auth=auth_required,
    tags=["webhooks"],
)
def retry_delivery(request, slug: str, delivery_id: int):
    event = _event(slug)
    delivery = get_object_or_404(
        WebhookDelivery.objects.select_related("webhook", "webhook__event"), pk=delivery_id, webhook__event=event
    )
    return delivery_out(services.retry(delivery, request.user))
