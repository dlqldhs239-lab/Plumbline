"""Signed records (T4): certificates for teams and participation records for
judges.

A record is a small JSON document and a signature over it. The document says
who, what and when; the signature lets this portal confirm later that the
document is one it issued and that nothing in it was changed. Records are
never deleted. One that should no longer stand is revoked, and says so to
anyone who checks it.
"""

import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse

from events.models import Event, Project

ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"  # no 0/O, 1/I/L, U: read aloud without mistakes


def new_serial() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"PL-{body[:4]}-{body[4:8]}-{body[8:]}"


class Record(models.Model):
    class Kind(models.TextChoices):
        PLACEMENT = "placement", "Placement"
        PARTICIPATION = "participation", "Participation"
        JUDGE = "judge", "Judging"

    serial = models.CharField(max_length=20, unique=True, default=new_serial, editable=False)
    kind = models.CharField(max_length=14, choices=Kind.choices)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="records")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="records")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="records")
    payload = models.JSONField()
    signature = models.CharField(max_length=64)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["kind", "id"]
        indexes = [models.Index(fields=["event", "recipient"])]

    def __str__(self) -> str:
        return f"{self.serial} {self.get_kind_display()} {self.payload.get('recipient', '')}"

    def get_absolute_url(self) -> str:
        return reverse("record_detail", args=[self.serial])

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
