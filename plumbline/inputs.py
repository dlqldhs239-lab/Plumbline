"""Reading untrusted input: ids, integers and the client address.

Anything that arrives as text and is about to be used as a number or a
database key goes through here first, so a malformed value is a refusal with
a reason and never an unhandled exception.
"""

from __future__ import annotations

import ipaddress
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404

MAX_ID = 2**31 - 1
_DIGITS = re.compile(r"^[0-9]{1,10}$")


def as_id(value) -> int | None:
    """A database id, or None when the text is not one."""
    text = str(value if value is not None else "").strip()
    if not _DIGITS.match(text):
        return None
    number = int(text)
    return number if 0 < number <= MAX_ID else None


def id_or_404(value) -> int:
    number = as_id(value)
    if number is None:
        raise Http404("No such record.")
    return number


def as_int(value, label: str, minimum: int | None = None, maximum: int | None = None) -> int:
    """A whole number inside the given bounds, or a ValidationError that says
    which value was wrong."""
    if isinstance(value, bool):
        raise ValidationError(f"{label} must be a whole number.")
    if isinstance(value, int):
        number = value
    else:
        text = str(value if value is not None else "").strip()
        if not re.match(r"^-?[0-9]{1,10}$", text):
            raise ValidationError(f"{label} must be a whole number.")
        number = int(text)
    if minimum is not None and number < minimum:
        raise ValidationError(f"{label} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise ValidationError(f"{label} must be at most {maximum}.")
    return number


def site_url(request, path: str = "/") -> str:
    """The address of a page as it should be printed for someone else:
    on a certificate, in a sign-in link, in an embed. Built from
    PLUMBLINE_SITE_URL when it is set, so that a request arriving with a
    forged Host header cannot put its own name on a certificate."""
    base = (settings.PLUMBLINE_SITE_URL or "").rstrip("/")
    if base:
        return base + (path if path.startswith("/") else "/" + path)
    return request.build_absolute_uri(path)


def client_ip(request) -> str:
    """The address the request came from.

    X-Forwarded-For is only believed when PLUMBLINE_TRUST_PROXY is on, because
    without a proxy in front anyone can write that header themselves. Whatever
    the source, the result is a valid address or an empty string.
    """
    if request is None:
        return ""
    candidate = ""
    if settings.PLUMBLINE_TRUST_PROXY:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
    candidate = candidate or request.META.get("REMOTE_ADDR", "") or ""
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return ""
