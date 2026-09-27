from __future__ import annotations

import json
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from .middleware import current_request
from .models import AuditLog


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def plain(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Make a detail dict storable whatever the caller put in it: decimals,
    datetimes and model instances become their text form. An audit entry
    must never be the reason a request fails."""
    return json.loads(json.dumps(detail or {}, cls=DjangoJSONEncoder, default=str))


def record(
    action: str,
    *,
    actor=None,
    event=None,
    target: models.Model | None = None,
    detail: dict[str, Any] | None = None,
    channel: str = "",
) -> AuditLog:
    """Append one audit entry. Actor and IP fall back to the current request."""
    request = current_request()
    if actor is None and request is not None and getattr(request, "user", None) is not None:
        actor = request.user if request.user.is_authenticated else None
    if not channel and request is not None:
        channel = "api" if request.path.startswith("/api/") else "ui"
    entry = AuditLog(
        actor=actor,
        actor_label=(actor.get_username() if actor else ""),
        event=event,
        action=action,
        detail=plain(detail),
        ip_address=_client_ip(request),
        channel=channel or "system",
    )
    if target is not None:
        entry.target_type = target._meta.label_lower
        entry.target_id = str(target.pk)
        entry.target_label = str(target)[:300]
    entry.save()
    if event is not None:
        # Imported here: integrations depends on audit, not the other way round.
        from integrations.services import enqueue

        enqueue(entry)
    return entry
