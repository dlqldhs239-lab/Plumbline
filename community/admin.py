from django.contrib import admin

from .models import Comment, Vote, Voter


@admin.register(Voter)
class VoterAdmin(admin.ModelAdmin):
    list_display = ("__str__", "event", "kind", "created_at", "last_seen_at", "voided_at", "flags")
    list_filter = ("event", "kind")
    search_fields = ("email", "user__username", "key")
    readonly_fields = ("key", "ballot_token", "ip_hash", "ua_hash")


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ("project", "voter", "weight", "created_at")
    list_filter = ("event",)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("project", "author", "created_at", "hidden_at")
    list_filter = ("project__event",)
