from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.services import record

from .models import ApiToken


class SignupForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        record("account.signup", actor=user, target=user)
        login(request, user)
        messages.success(request, "Welcome to Plumbline.")
        return redirect(request.GET.get("next") or "dashboard")
    return render(request, "accounts/signup.html", {"form": form})


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
