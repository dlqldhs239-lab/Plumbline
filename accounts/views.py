from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from audit.services import record

from .models import ApiToken, SignInLink

BACKEND = "accounts.backends.EmailOrUsernameBackend"


def safe_next(request, default="dashboard"):
    """Where to go after signing in. Only paths on this site are honoured, so
    a crafted link cannot send someone elsewhere after they log in."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return default


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email already exists. If an organizer invited you, ask them for your sign-in link."
            )
        return email


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        record("account.signup", actor=user, target=user)
        login(request, user, backend=BACKEND)
        messages.success(request, "Welcome to Plumbline.")
        return redirect(safe_next(request))
    return render(request, "accounts/signup.html", {"form": form, "next": request.GET.get("next", "")})


@never_cache
def claim(request, token):
    """Open a one-time sign-in link: choose a password, and you are in."""
    link = SignInLink.find(token)
    if link is None:
        return render(request, "accounts/claim_invalid.html", status=410)
    form = SetPasswordForm(link.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked = SignInLink.objects.select_for_update().get(pk=link.pk)
            if locked.used_at is not None:
                return render(request, "accounts/claim_invalid.html", status=410)
            form.save()
            locked.used_at = timezone.now()
            locked.save(update_fields=["used_at"])
        record("account.claim", actor=link.user, target=link.user)
        login(request, link.user, backend=BACKEND)
        messages.success(request, "Password set. You are signed in.")
        return redirect("dashboard")
    return render(request, "accounts/claim.html", {"form": form, "account": link.user})


def password_help(request):
    return render(request, "accounts/password_help.html")


@login_required
def tokens(request):
    new_raw = None
    if request.method == "POST":
        label = (request.POST.get("label") or "")[:100]
        token, new_raw = ApiToken.issue(request.user, label=label)
        record("token.issue", target=token, detail={"label": label})
        messages.success(request, "Token created. Copy it now; it will not be shown again.")
    items = request.user.api_tokens.all()
    return render(request, "accounts/tokens.html", {"tokens": items, "new_raw": new_raw})


@login_required
@require_POST
def revoke_token(request, pk):
    token = get_object_or_404(ApiToken, pk=pk, user=request.user, revoked_at__isnull=True)
    token.revoked_at = timezone.now()
    token.save(update_fields=["revoked_at"])
    record("token.revoke", target=token)
    messages.info(request, "Token revoked.")
    return redirect("accounts:tokens")
