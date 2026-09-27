"""Reading the installation's settings from the environment. No Django
imports beyond the exception, so the settings module can use it."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured


def site_address(text: str) -> str:
    """PLUMBLINE_SITE_URL as it will be printed on certificates and in
    sign-in links: empty, or an http or https address with a host and nothing
    after it but a path. Anything else stops the portal at start, which is
    better than a certificate that sends people to a broken address."""
    text = (text or "").strip().rstrip("/")
    if not text:
        return ""
    wrong = ImproperlyConfigured(
        f"PLUMBLINE_SITE_URL is {text!r}. It must be the address people type to reach this portal, "
        "with its scheme, such as https://judging.example.org"
    )
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise wrong from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise wrong
    if parts.username or parts.password or parts.query or parts.fragment or port == 0:
        raise wrong
    if any(ch.isspace() or ch in "<>\"'\\" for ch in text):
        raise wrong
    return text
