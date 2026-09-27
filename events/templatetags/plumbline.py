import re

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

SAFE_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


@register.simple_tag
def theme_style(event=None):
    """Inline the four theme colours for an event page. Values are validated
    hex colours, re-checked here so nothing else can reach the style block."""
    if event is None or not hasattr(event, "theme_variables"):
        return ""
    pairs = [(k, v) for k, v in event.theme_variables().items() if SAFE_COLOUR.match(v)]
    if len(pairs) != 8:
        return ""
    body = ";".join(f"{k}:{v}" for k, v in pairs)
    return mark_safe(f"<style>:root[data-mode]{{{body}}}</style>")  # noqa: S308 - values matched against SAFE_COLOUR


@register.simple_tag
def theme_mode(event=None):
    if event is None or not hasattr(event, "theme_mode"):
        return "dark"
    return event.theme_mode


@register.filter
def cover_style(title):
    """Where a project's plumb line hangs on its cover. Derived from the title,
    so a project always looks the same and forty of them look different."""
    import hashlib

    h = hashlib.sha256(str(title or "").encode()).digest()
    x = 18 + h[0] % 66  # line position, percent from the left
    y = 30 + h[1] % 38  # line length, percent of the height
    w = 62 + h[2] % 22  # width axis of the title
    return f"--x:{x}%;--y:{y}%;--w:{w}%"


@register.filter
def initials(value):
    words = [w for w in re.split(r"\s+", str(value or "").strip()) if w]
    return "".join(w[0] for w in words[:2]).upper() or "—"
