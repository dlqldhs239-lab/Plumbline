"""Community voting and comments (T3).

A Voter is one ballot-holder for one event. How they were admitted depends on
the event's voting_access:

- open:  anyone with the link; identified by a signed cookie, with the client
         IP and user-agent hashed for duplicate detection.
- email: the organizer generates one ballot link per email address and sends
         them with their own mail tool (the portal has no outbound mail and
         needs none). Opening the link binds the cookie to that voter.
- auth:  signed-in users; one voter per user.

Votes are never deleted. Voiding a voter keeps the rows and marks them, so an
organizer can read what happened.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from events.models import Event, Project


class Voter(models.Model):
    class Kind(models.TextChoices):
        OPEN = "open", "Open link"
        EMAIL = "email", "Email ballot link"
        AUTH = "auth", "Signed-in user"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="voters")
    kind = models.CharField(max_length=6, choices=Kind.choices)
    key = models.CharField(max_length=64, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="voter_identities")
    email = models.EmailField(blank=True)
    ballot_token = models.CharField(max_length=64, blank=True, db_index=True)
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    ua_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)
    flags = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="uniq_voter_user", condition=models.Q(user__isnull=False)),
            models.UniqueConstraint(fields=["event", "email"], name="uniq_voter_email", condition=~models.Q(email="")),
        ]

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        label = self.email or (self.user.get_username() if self.user else self.key[:8])
        return f"{label} ({self.kind})"

    @property
    def is_voided(self) -> bool:
        return self.voided_at is not None

    def credits_used(self) -> int:
        return sum(v.weight * v.weight for v in self.votes.all())


class Vote(models.Model):
    """`weight` is how many votes this voter put on the project. With
    quadratic voting the cost is weight² credits; otherwise weight is 0 or 1."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="votes")
    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="votes")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="votes")
    weight = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["voter", "project"], name="uniq_vote_per_project")]
        indexes = [models.Index(fields=["event", "project"])]

    def __str__(self) -> str:
        return f"{self.voter} → {self.project} ×{self.weight}"


class Comment(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_comments")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.author.get_username()} on {self.project.title}"

    @property
    def is_hidden(self) -> bool:
        return self.hidden_at is not None

    def hide(self, by):
        self.hidden_at = timezone.now()
        self.hidden_by = by
