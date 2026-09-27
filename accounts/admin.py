from django.contrib import admin, messages

from plumbline.inputs import site_url

from .models import ApiToken, SignInLink


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ("prefix", "user", "label", "created_at", "last_used_at", "revoked_at")
    readonly_fields = ("prefix", "key_hash", "created_at", "last_used_at")
    search_fields = ("user__username", "user__email", "label", "prefix")


@admin.register(SignInLink)
class SignInLinkAdmin(admin.ModelAdmin):
    """Add one to recover an account: pick the user, save, and the link is
    shown once at the top of the page."""

    list_display = ("user", "created_by", "created_at", "expires_at", "used_at")
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ()
    fields = ("user",)

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        link, raw = SignInLink.issue(obj.user, created_by=request.user)
        obj.pk = link.pk
        url = site_url(request, f"/accounts/claim/{raw}/")
        messages.warning(request, f"Sign-in link for {obj.user}: {url} (shown once; works once, for seven days)")
