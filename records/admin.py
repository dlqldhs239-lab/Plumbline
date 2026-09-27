from django.contrib import admin

from .models import Record


@admin.register(Record)
class RecordAdmin(admin.ModelAdmin):
    """Read-only: a record edited here would stop matching its signature."""

    list_display = ("serial", "kind", "event", "recipient", "issued_at", "revoked_at")
    list_filter = ("kind", "event")
    search_fields = ("serial", "recipient__username", "recipient__email")
    readonly_fields = [f.name for f in Record._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
