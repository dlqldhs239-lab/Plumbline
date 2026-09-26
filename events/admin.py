from django.contrib import admin

from .models import CustomAnswer, CustomQuestion, Event, EventRole, Prize, Project, Team, TeamInvite, TeamMembership, Track


class TrackInline(admin.TabularInline):
    model = Track
    extra = 0


class PrizeInline(admin.TabularInline):
    model = Prize
    extra = 0


class CustomQuestionInline(admin.TabularInline):
    model = CustomQuestion
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "submissions_open_at", "submissions_close_at", "results_published_at", "is_listed")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [TrackInline, PrizeInline, CustomQuestionInline]
    search_fields = ("name", "slug")


@admin.register(EventRole)
class EventRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "event", "role", "external_id")
    list_filter = ("role", "event")
    search_fields = ("user__username", "user__email", "external_id")
    filter_horizontal = ("tracks",)


class MembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 0


class InviteInline(admin.TabularInline):
    model = TeamInvite
    extra = 0
    readonly_fields = ("token", "uses")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "external_id", "created_at")
    list_filter = ("event",)
    search_fields = ("name", "external_id")
    inlines = [MembershipInline, InviteInline]


class AnswerInline(admin.TabularInline):
    model = CustomAnswer
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "team", "track", "status", "is_hidden", "duplicate_of", "submitted_at")
    list_filter = ("event", "status", "is_hidden", "track")
    search_fields = ("title", "tagline", "team__name", "external_id")
    inlines = [AnswerInline]
