from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from events.models import Event
from events.permissions import is_organizer

from . import services
from .models import Webhook, WebhookDelivery


@login_required
def organize_integrations(request, slug):
    event = get_object_or_404(Event, slug=slug)
    if not is_organizer(request.user, event):
        raise PermissionDenied("Organizer role required.")
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "create":
                hook = services.create_webhook(
                    event,
                    request.user,
                    request.POST.get("url", ""),
                    request.POST.get("actions", ""),
                    request.POST.get("description", ""),
                )
                messages.success(request, f"Webhook created. Signing secret: {hook.secret}")
            elif action in ("enable", "disable", "delete", "test"):
                hook = get_object_or_404(Webhook, pk=request.POST.get("webhook"), event=event)
                if action == "delete":
                    services.delete_webhook(hook, request.user)
                    messages.info(request, "Webhook deleted.")
                elif action == "test":
                    d = services.send_test(hook, request.user)
                    if d.status == WebhookDelivery.Status.OK:
                        messages.success(request, f"Ping delivered ({d.status_code}).")
                    else:
                        messages.error(request, f"Ping failed: {d.status_code or d.error}")
                else:
                    services.set_active(hook, request.user, action == "enable")
            elif action == "retry":
                d = get_object_or_404(WebhookDelivery, pk=request.POST.get("delivery"), webhook__event=event)
                d = services.retry(d, request.user)
                if d.status == WebhookDelivery.Status.OK:
                    messages.success(request, "Delivered.")
                else:
                    messages.error(request, f"Still failing: {d.status_code or d.error}")
        except ValidationError as e:
            messages.error(request, "; ".join(e.messages))
        return redirect("organize_integrations", slug=slug)
    hooks = event.webhooks.all()
    deliveries = WebhookDelivery.objects.filter(webhook__event=event).select_related("webhook")[:50]
    actions = list(event.audit_entries.values_list("action", flat=True).distinct().order_by("action"))
    return render(
        request,
        "events/organize/integrations.html",
        {
            "event": event,
            "hooks": hooks,
            "deliveries": deliveries,
            "known_actions": actions,
            "embed_url": request.build_absolute_uri(f"/events/{event.slug}/embed/gallery/"),
        },
    )
