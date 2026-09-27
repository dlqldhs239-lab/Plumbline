"""Webhooks (T4).

Every state change in Plumbline already passes through audit.record(), so a
webhook is a subscription to the audit stream: when an entry is written for
an event, each matching webhook gets one delivery. Deliveries are rows, not
fire-and-forget requests, so an organizer can see what was sent, what came
back, and retry.
"""

import secrets

from django.conf import settings
from django.db import models

from audit.models import AuditLog
from events.models import Event


def new_secret() -> str:
    return secrets.token_urlsafe(32)


class Webhook(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="webhooks")
    url = models.URLField(max_length=500)
    description = models.CharField(max_length=200, blank=True)
    secret = models.CharField(max_length=64, default=new_secret, editable=False)
    actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Action prefixes to send, e.g. project. or score.submit. Empty means everything.",
    )
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.url

    def wants(self, action: str) -> bool:
        if not self.active:
            return False
        if not self.actions:
            return True
        return any(action.startswith(prefix) for prefix in self.actions)


class WebhookDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        OK = "ok", "Delivered"
        FAILED = "failed", "Failed"

    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    entry = models.ForeignKey(AuditLog, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=80)
    payload = models.JSONField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    response_excerpt = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.action} → {self.webhook.url} ({self.status})"
