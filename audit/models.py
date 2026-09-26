from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """An append-only record of who did what.

    Written by the service layer, never by templates or the API directly, so the
    same action is logged whether it came from the UI, the API or a management
    command. Organizers read it from the UI; it is exported as CSV too.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=200, blank=True)
    event = models.ForeignKey(
        "events.Event", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    target_label = models.CharField(max_length=300, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    channel = models.CharField(max_length=20, blank=True)  # ui | api | seed | admin

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["event", "created_at"])]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_label or 'system'} {self.action}"
