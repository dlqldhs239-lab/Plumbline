"""Webhook subscription and delivery.

Signing: the request body is signed with HMAC-SHA256 using the webhook's
secret and sent as `X-Plumbline-Signature: sha256=<hex>`. A receiver
recomputes it over the raw body to know the request came from this portal.

Failure handling: a delivery that does not get a 2xx is marked failed with
the status code or error. It can be retried from the console, the API, or
`manage.py deliver_webhooks`, up to PLUMBLINE_WEBHOOK_MAX_ATTEMPTS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, transaction
from django.utils import timezone

from events.models import Event
from events.permissions import is_organizer

from .models import Webhook, WebhookDelivery

USER_AGENT = "Plumbline-Webhook/1.0"
# Webhook bookkeeping is itself audited, but is not sent to webhooks: a hook
# that reports its own creation and retries is noise at best, a loop at worst.
UNSENT_PREFIXES = ("webhook.",)


def payload_for(entry) -> dict:
    """What a receiver gets. The client IP stays in the audit log only."""
    return {
        "id": entry.pk,
        "event": entry.event.slug if entry.event_id else None,
        "action": entry.action,
        "at": entry.created_at.isoformat(),
        "actor": entry.actor_label or None,
        "channel": entry.channel,
        "target": {"type": entry.target_type, "id": entry.target_id, "label": entry.target_label},
        "detail": entry.detail,
    }


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(secret, body), signature or "")


def enqueue(entry) -> list[WebhookDelivery]:
    """Create one delivery per matching webhook and send after commit."""
    if entry.event_id is None or entry.action.startswith(UNSENT_PREFIXES):
        return []
    hooks = [h for h in Webhook.objects.filter(event_id=entry.event_id, active=True) if h.wants(entry.action)]
    if not hooks:
        return []
    payload = payload_for(entry)
    deliveries = [
        WebhookDelivery.objects.create(webhook=h, entry=entry, action=entry.action, payload=payload) for h in hooks
    ]
    ids = [d.pk for d in deliveries]
    transaction.on_commit(lambda: _dispatch(ids))
    return deliveries


def _dispatch(delivery_ids: list[int]):
    if settings.PLUMBLINE_WEBHOOKS_ASYNC:
        threading.Thread(target=_deliver_many, args=(delivery_ids, True), daemon=True).start()
    else:
        _deliver_many(delivery_ids, False)


def _deliver_many(delivery_ids: list[int], own_thread: bool):
    try:
        for pk in delivery_ids:
            delivery = WebhookDelivery.objects.select_related("webhook").filter(pk=pk).first()
            if delivery is not None:
                deliver(delivery)
    finally:
        if own_thread:
            close_old_connections()


def _post(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, str]:
    """Send one request. Returns (status, first 300 chars of the response).
    Separated so tests can replace the network."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace")


def deliver(delivery: WebhookDelivery) -> WebhookDelivery:
    hook = delivery.webhook
    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-Plumbline-Event": delivery.action,
        "X-Plumbline-Delivery": str(delivery.pk),
        "X-Plumbline-Signature": sign(hook.secret, body),
    }
    delivery.attempts += 1
    delivery.last_attempt_at = timezone.now()
    delivery.error = ""
    try:
        status, excerpt = _post(hook.url, body, headers, settings.PLUMBLINE_WEBHOOK_TIMEOUT)
        delivery.status_code = status
        delivery.response_excerpt = excerpt[:300]
        delivery.status = WebhookDelivery.Status.OK if 200 <= status < 300 else WebhookDelivery.Status.FAILED
    except Exception as e:  # noqa: BLE001 - any network failure is a failed delivery, never a crash
        delivery.status_code = None
        delivery.status = WebhookDelivery.Status.FAILED
        delivery.error = f"{type(e).__name__}: {e}"[:300]
    delivery.save()
    return delivery


def retry(delivery: WebhookDelivery, user) -> WebhookDelivery:
    if not is_organizer(user, delivery.webhook.event):
        raise PermissionDenied("Only organizers can retry deliveries.")
    if delivery.status == WebhookDelivery.Status.OK:
        raise ValidationError("That delivery already succeeded.")
    if delivery.attempts >= settings.PLUMBLINE_WEBHOOK_MAX_ATTEMPTS:
        raise ValidationError(f"Gave up after {delivery.attempts} attempts. Fix the receiver and create a new webhook.")
    return deliver(delivery)


def retry_failed(max_attempts: int | None = None) -> dict:
    """Used by `manage.py deliver_webhooks`."""
    max_attempts = max_attempts or settings.PLUMBLINE_WEBHOOK_MAX_ATTEMPTS
    qs = WebhookDelivery.objects.select_related("webhook").filter(
        status__in=[WebhookDelivery.Status.PENDING, WebhookDelivery.Status.FAILED],
        attempts__lt=max_attempts,
        webhook__active=True,
    )
    out = {"tried": 0, "ok": 0, "failed": 0}
    for delivery in qs.order_by("created_at"):
        deliver(delivery)
        out["tried"] += 1
        out["ok" if delivery.status == WebhookDelivery.Status.OK else "failed"] += 1
    return out


# --- management ------------------------------------------------------------------


def _clean_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValidationError("Webhook URL must be an http or https address.")
    return url


def _clean_actions(actions) -> list[str]:
    if isinstance(actions, str):
        actions = actions.replace(",", "\n").splitlines()
    return [a.strip() for a in (actions or []) if a and a.strip()]


@transaction.atomic
def create_webhook(event: Event, user, url: str, actions=None, description: str = "") -> Webhook:
    from audit.services import record

    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can manage webhooks.")
    hook = Webhook.objects.create(
        event=event,
        url=_clean_url(url),
        actions=_clean_actions(actions),
        description=(description or "")[:200],
        created_by=user,
    )
    record("webhook.create", actor=user, event=event, target=hook, detail={"actions": hook.actions})
    return hook


@transaction.atomic
def set_active(hook: Webhook, user, active: bool) -> Webhook:
    from audit.services import record

    if not is_organizer(user, hook.event):
        raise PermissionDenied("Only organizers can manage webhooks.")
    hook.active = active
    hook.save(update_fields=["active"])
    record("webhook.enable" if active else "webhook.disable", actor=user, event=hook.event, target=hook)
    return hook


@transaction.atomic
def delete_webhook(hook: Webhook, user):
    from audit.services import record

    if not is_organizer(user, hook.event):
        raise PermissionDenied("Only organizers can manage webhooks.")
    record("webhook.delete", actor=user, event=hook.event, target=hook, detail={"deliveries": hook.deliveries.count()})
    hook.delete()


def send_test(hook: Webhook, user) -> WebhookDelivery:
    """A ping, so an organizer can check the receiver before the event starts."""
    if not is_organizer(user, hook.event):
        raise PermissionDenied("Only organizers can manage webhooks.")
    payload = {
        "id": None,
        "event": hook.event.slug,
        "action": "webhook.ping",
        "at": timezone.now().isoformat(),
        "actor": user.get_username(),
        "channel": "system",
        "target": {"type": "integrations.webhook", "id": str(hook.pk), "label": hook.url},
        "detail": {"message": "Plumbline can reach you."},
    }
    delivery = WebhookDelivery.objects.create(webhook=hook, action="webhook.ping", payload=payload)
    return deliver(delivery)
