import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class ApiToken(models.Model):
    """A bearer token for the REST API.

    Only a SHA-256 hash is stored. The raw value is shown once at issue time
    (or printed by the seed command for the fixture users).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    label = models.CharField(max_length=100, blank=True)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    prefix = models.CharField(max_length=12, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.prefix}… ({self.user.get_username()})"

    @staticmethod
    def hash_key(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, user, label: str = "", raw: str | None = None) -> tuple["ApiToken", str]:
        """Create a token and return (token, raw_value)."""
        raw = raw or f"pl_{secrets.token_urlsafe(32)}"
        token = cls.objects.create(user=user, label=label, key_hash=cls.hash_key(raw), prefix=raw[:12])
        return token, raw

    @classmethod
    def deterministic_raw(cls, slot: str) -> str:
        """A reproducible token for seeded users, derived from the seed secret.

        Used only by `seed_fixtures` so that a committed `.dogfood.toml` keeps
        working after the database is recreated. Change PLUMBLINE_SEED_SECRET
        in any real deployment.
        """
        digest = hmac.new(settings.PLUMBLINE_SEED_SECRET.encode(), slot.encode(), hashlib.sha256).hexdigest()
        return f"pl_{slot}_{digest[:32]}"

    @classmethod
    def authenticate(cls, raw: str):
        """Return the user for a raw token, or None. Updates last_used_at."""
        if not raw:
            return None
        try:
            token = cls.objects.select_related("user").get(key_hash=cls.hash_key(raw), revoked_at__isnull=True)
        except cls.DoesNotExist:
            return None
        if not token.user.is_active:
            return None
        cls.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return token.user


class SignInLink(models.Model):
    """A one-time link that lets someone set a password and sign in.

    The portal sends no email, so this is how an invited judge gets in and how
    a forgotten password is recovered: an organizer or admin creates the link
    and passes it on through whatever channel they already use. Only a hash
    is stored; the link is shown once, expires, and works once.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sign_in_links")
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"sign-in link for {self.user.get_username()}"

    @classmethod
    def issue(cls, user, created_by=None, days: int = 7) -> tuple["SignInLink", str]:
        """Create a link and return (link, raw token). Earlier unused links for
        the same user stop working, so only the newest one is live."""
        cls.objects.filter(user=user, used_at__isnull=True).update(expires_at=timezone.now())
        raw = secrets.token_urlsafe(32)
        link = cls.objects.create(
            user=user,
            key_hash=ApiToken.hash_key(raw),
            created_by=created_by,
            expires_at=timezone.now() + timedelta(days=days),
        )
        return link, raw

    @classmethod
    def find(cls, raw: str):
        """The live link for a raw token, or None."""
        link = cls.objects.select_related("user").filter(key_hash=ApiToken.hash_key(raw or "")).first()
        if link is None or link.used_at is not None or link.expires_at <= timezone.now() or not link.user.is_active:
            return None
        return link
