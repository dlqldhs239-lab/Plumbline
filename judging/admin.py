from django.contrib import admin

from .models import Criterion, JudgeAssignment, JudgeCalibration, ProjectResult, Rubric, Score


class CriterionInline(admin.TabularInline):
    model = Criterion
    extra = 0


@admin.register(Rubric)
class RubricAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "scale_min", "scale_max")
    inlines = [CriterionInline]


class ScoreInline(admin.TabularInline):
    model = Score
    extra = 0


@admin.register(JudgeAssignment)
class JudgeAssignmentAdmin(admin.ModelAdmin):
    list_display = ("judge", "project", "event", "batch", "status", "submitted_at")
    list_filter = ("event", "status", "batch")
    search_fields = ("judge__username", "project__title")
    inlines = [ScoreInline]


@admin.register(ProjectResult)
class ProjectResultAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "event",
        "review_count",
        "raw_mean",
        "normalized_mean",
        "rank_raw",
        "rank_normalized",
        "method",
    )
    list_filter = ("event",)


@admin.register(JudgeCalibration)
class JudgeCalibrationAdmin(admin.ModelAdmin):
    list_display = ("judge", "event", "review_count", "mean", "stdev", "shrink_weight", "flat")
    list_filter = ("event", "flat")
