from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from events.models import Event, Project


class Rubric(models.Model):
    """One rubric per event. Criteria carry weights; the scale is shared."""

    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="rubric")
    name = models.CharField(max_length=120, default="Judging rubric")
    scale_min = models.PositiveSmallIntegerField(default=1)
    scale_max = models.PositiveSmallIntegerField(default=5)
    instructions = models.TextField(blank=True)
    jury_k = models.PositiveSmallIntegerField(
        "Jury-size adjustment",
        null=True,
        blank=True,
        validators=[MaxValueValidator(100)],
        help_text=(
            "How many reviews the panel mean counts for when a project has few. "
            "Empty uses the event's reviews per project; 0 switches the adjustment off."
        ),
    )

    def __str__(self) -> str:
        return f"{self.name} ({self.event.slug})"

    def effective_jury_k(self) -> float:
        if self.jury_k is None:
            return float(self.event.reviews_per_project)
        return float(self.jury_k)

    @property
    def total_weight(self) -> Decimal:
        return sum((c.weight for c in self.criteria.all()), Decimal("0"))


class Criterion(models.Model):
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    key = models.SlugField(max_length=40)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    weight = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("1.00"), validators=[MinValueValidator(Decimal("0"))]
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        constraints = [models.UniqueConstraint(fields=["rubric", "key"], name="uniq_criterion_key")]

    def __str__(self) -> str:
        return f"{self.name} (×{self.weight})"


class JudgeAssignment(models.Model):
    """A judge is asked to review a project. Scores hang off the assignment,
    so a judge can only ever write scores into their own assignments."""

    class Status(models.TextChoices):
        PENDING = "pending", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        SUBMITTED = "submitted", "Submitted"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="assignments")
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="judge_assignments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="assignments")
    batch = models.CharField(max_length=40, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    comment = models.TextField(blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["judge", "project"], name="uniq_judge_project")]
        indexes = [models.Index(fields=["event", "judge", "status"])]

    def __str__(self) -> str:
        return f"{self.judge.get_username()} → {self.project.title}"

    def mark_submitted(self):
        self.status = self.Status.SUBMITTED
        self.submitted_at = timezone.now()


class Score(models.Model):
    assignment = models.ForeignKey(JudgeAssignment, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name="scores")
    value = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "criterion"], name="uniq_score_per_criterion")]

    def __str__(self) -> str:
        return f"{self.criterion.key}={self.value}"


class ProjectResult(models.Model):
    """Computed standings. Recomputed by an organizer; a snapshot, not a source
    of truth. `method` documents which normalization produced it."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="results")
    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="result")
    review_count = models.PositiveSmallIntegerField(default=0)
    raw_mean = models.FloatField(null=True, blank=True)
    normalized_mean = models.FloatField(null=True, blank=True)
    rank_raw = models.PositiveIntegerField(null=True, blank=True)
    rank_normalized = models.PositiveIntegerField(null=True, blank=True)
    # The score that decides the ranking: normalized, then adjusted for how
    # many reviews the project received.
    adjusted_mean = models.FloatField(null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    criterion_means = models.JSONField(default=list, blank=True)
    # The constant this row was computed with, so the page can say what was
    # done and the console can tell when the setting has moved on.
    jury_k = models.FloatField(null=True, blank=True)
    community_score = models.FloatField(null=True, blank=True)
    method = models.CharField(max_length=60, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Unranked projects last on every database (SQLite would put them first).
        ordering = [
            models.F("rank").asc(nulls_last=True),
            models.F("rank_normalized").asc(nulls_last=True),
            "id",
        ]

    @property
    def moved(self) -> int | None:
        """Places gained (positive) or lost between the raw mean and the final rank."""
        if self.rank is None or self.rank_raw is None:
            return None
        return self.rank_raw - self.rank


class JudgeCalibration(models.Model):
    """Per-judge statistics from the last normalization run, kept so an
    organizer can see who scores flat or harsh and read the maths."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="calibrations")
    judge = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    review_count = models.PositiveSmallIntegerField(default=0)
    mean = models.FloatField(null=True, blank=True)
    stdev = models.FloatField(null=True, blank=True)
    shrink_weight = models.FloatField(null=True, blank=True)
    shrunk_mean = models.FloatField(null=True, blank=True)
    shrunk_stdev = models.FloatField(null=True, blank=True)
    flat = models.BooleanField(default=False)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "judge"], name="uniq_calibration")]
