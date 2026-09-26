from django.contrib import admin

from .models import ApiToken


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("prefix", "user", "label", "created_at", "last_used_at", "revoked_at")
    readonly_fields = ("prefix", "key_hash", "created_at", "last_used_at")
    search_fields = ("user__username", "user__email", "label", "prefix")
