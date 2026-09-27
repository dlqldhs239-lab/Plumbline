from django.contrib import admin

from .models import Webhook, WebhookDelivery


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ("url", "event", "active", "actions", "created_at")
    list_filter = ("event", "active")
    readonly_fields = ("secret",)


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("action", "webhook", "status", "status_code", "attempts", "created_at")
    list_filter = ("status", "webhook__event")
    readonly_fields = [f.name for f in WebhookDelivery._meta.fields]
