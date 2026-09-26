import secrets

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Role(models.TextChoices):
    PARTICIPANT = "participant", "Participant"
    JUDGE = "judge", "Judge"
    ORGANIZER = "organizer", "Organizer"


class Event(models.Model):
    """A hackathon. Dates are the single source of truth for what is allowed
    when; every open/closed decision goes through the methods below."""

    class VotingAccess(models.TextChoices):
        CLOSED = "closed", "No community voting"
        OPEN = "open", "Anyone with the link"
        EMAIL = "email", "Email-verified voters"
        AUTHENTICATED = "auth", "Signed-in users only"

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    tagline = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    external_id = models.CharField(max_length=80, blank=True, db_index=True)

    submissions_open_at = models.DateTimeField()
    submissions_close_at = models.DateTimeField()
    judging_open_at = models.DateTimeField(null=True, blank=True)
    judging_close_at = models.DateTimeField(null=True, blank=True)
    results_published_at = models.DateTimeField(null=True, blank=True)

    voting_access = models.CharField(max_length=10, choices=VotingAccess.choices, default=VotingAccess.CLOSED)
    voting_open_at = models.DateTimeField(null=True, blank=True)
    voting_close_at = models.DateTimeField(null=True, blank=True)
    voting_credits = models.PositiveSmallIntegerField(
        default=0,
        help_text="Quadratic voting budget per voter. 0 means one vote per project.",
    )
    comments_enabled = models.BooleanField(default=True)

    reviews_per_project = models.PositiveSmallIntegerField(default=3)
    is_listed = models.BooleanField(default=True, help_text="Show on the public home page")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submissions_close_at"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("event_detail", args=[self.slug])

    # --- lifecycle -----------------------------------------------------
    def submissions_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.submissions_open_at <= now < self.submissions_close_at

    def submissions_closed(self, now=None) -> bool:
        now = now or timezone.now()
        return now >= self.submissions_close_at

    def judging_open(self, now=None) -> bool:
        now = now or timezone.now()
        start = self.judging_open_at or self.submissions_close_at
        if now < start:
            return False
        return self.judging_close_at is None or now < self.judging_close_at

    def voting_open(self, now=None) -> bool:
        if self.voting_access == self.VotingAccess.CLOSED:
            return False
        now = now or timezone.now()
        if self.voting_open_at and now < self.voting_open_at:
            return False
        if self.voting_close_at and now >= self.voting_close_at:
            return False
        return True

    @property
    def results_published(self) -> bool:
        return self.results_published_at is not None

    def phase(self, now=None) -> str:
        now = now or timezone.now()
        if now < self.submissions_open_at:
            return "upcoming"
        if self.submissions_open(now):
            return "submissions"
        if self.results_published:
            return "results"
        if self.judging_open(now):
            return "judging"
        return "closed"


class Track(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    external_id = models.CharField(max_length=80, blank=True, db_index=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["event", "name"], name="uniq_track_name_per_event")]

    def __str__(self) -> str:
        return self.name


class Prize(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    track = models.ForeignKey(Track, null=True, blank=True, on_delete=models.SET_NULL, related_name="prizes")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.name


class EventRole(models.Model):
    """Membership of a user in an event with one role. Judges carry the tracks
    they may see; an empty set means every track."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_roles")
    role = models.CharField(max_length=12, choices=Role.choices)
    tracks = models.ManyToManyField(Track, blank=True, related_name="judge_roles")
    external_id = models.CharField(max_length=80, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "user", "role"], name="uniq_event_user_role")]

    def __str__(self) -> str:
        return f"{self.user.get_username()} as {self.role} in {self.event.slug}"


class Team(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=120)
    external_id = models.CharField(max_length=80, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Team names are not unique: real events get three teams called the
        # same thing (the fixture set has them). Identity is the id.
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name

    def member_users(self):
        return [m.user for m in self.memberships.select_related("user")]


class TeamMembership(models.Model):
    class MemberRole(models.TextChoices):
        OWNER = "owner", "Owner"
        MEMBER = "member", "Member"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")
    role = models.CharField(max_length=8, choices=MemberRole.choices, default=MemberRole.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "user"], name="uniq_team_member")]

    def __str__(self) -> str:
        return f"{self.user.get_username()} in {self.team.name}"


class TeamInvite(models.Model):
    """A shareable join link. Tokens are random, expire, and can be capped."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invites")
    token = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveSmallIntegerField(default=10)
    uses = models.PositiveSmallIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(24)
        super().save(*args, **kwargs)

    def is_valid(self, now=None) -> bool:
        now = now or timezone.now()
        if self.revoked_at:
            return False
        if self.expires_at and now >= self.expires_at:
            return False
        return self.uses < self.max_uses

    def get_absolute_url(self) -> str:
        return reverse("team_join", args=[self.token])


class Project(models.Model):
    """A submission. Drafts are private to the team; submitted projects are
    public in the gallery unless hidden by an organizer."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        WITHDRAWN = "withdrawn", "Withdrawn"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="projects")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="projects")
    track = models.ForeignKey(Track, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    external_id = models.CharField(max_length=80, blank=True, db_index=True)

    title = models.CharField(max_length=200)
    tagline = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    thumbnail_url = models.URLField(max_length=500, blank=True)
    image_urls = models.JSONField(default=list, blank=True)
    demo_video_url = models.URLField(max_length=500, blank=True)
    repo_url = models.URLField(max_length=500, blank=True)
    live_url = models.URLField(max_length=500, blank=True)
    tech_tags = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    is_hidden = models.BooleanField(default=False, help_text="Hidden from the gallery by an organizer")
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-submitted_at", "-created_at"]
        indexes = [models.Index(fields=["event", "status", "is_hidden"])]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("project_detail", args=[self.event.slug, self.pk])

    @property
    def is_public(self) -> bool:
        return self.status == self.Status.SUBMITTED and not self.is_hidden

    @property
    def tags_display(self) -> str:
        return ", ".join(self.tech_tags or [])


class CustomQuestion(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Short text"
        TEXTAREA = "textarea", "Long text"
        URL = "url", "URL"
        CHOICE = "choice", "Single choice"
        CHECKBOX = "checkbox", "Yes / no"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="custom_questions")
    prompt = models.CharField(max_length=300)
    help_text = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.TEXT)
    choices = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.prompt


class CustomAnswer(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(CustomQuestion, on_delete=models.CASCADE, related_name="answers")
    value = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "question"], name="uniq_answer_per_question")]
