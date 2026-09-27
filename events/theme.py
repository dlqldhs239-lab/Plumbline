"""Per-event colour themes.

An organizer runs a dozen events a year and each has its own identity, so an
event carries four colours: ground, ink, accent and signal. Everything else
in the stylesheet is derived from those with color-mix(). A theme is refused
at save time if it would make text unreadable; the numbers are WCAG contrast
ratios computed here, with the same mixing the stylesheet does.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# name: (label, ground, ink, accent, signal)
PRESETS: dict[str, tuple[str, str, str, str, str]] = {
    "plumbline": ("Plumbline dark", "#080C18", "#E9EEFF", "#00E5D0", "#FF3D6E"),
    "drafting": ("Drafting film (light)", "#E9ECEA", "#10161B", "#1F40C4", "#B02A2A"),
    "amber": ("Black and amber", "#0A0A0A", "#EDEDED", "#F5A524", "#FF6B5E"),
    "oxblood": ("Cream and oxblood (light)", "#F4F1EA", "#14171C", "#7A2323", "#A3341F"),
    "cyanotype": ("Cyanotype", "#0E3C86", "#F2F6FF", "#FFD94A", "#FFB3A8"),
    "ember": ("Charcoal and red", "#111111", "#EEEEEE", "#FF6B5E", "#F5A524"),
}
DEFAULT = "plumbline"

MIN_TEXT = 4.5  # body text
MIN_INK = 7.0  # the main text colour should be comfortably readable
MUTE_TARGET = 5.0  # contrast the secondary grey is tuned to reach
MUTE_FLOOR = 0.54  # share of ink in --mute on a very dark ground, as in plumbline.css


def _rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def luminance(value: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, share_of_a: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x * share_of_a + y * (1 - share_of_a) for x, y in zip(ra, rb, strict=True)))


def mute_share(ground: str, ink: str) -> float:
    """Smallest share of ink that gives the secondary grey MUTE_TARGET contrast.
    A mid-tone ground (a cyanotype blue, say) needs far more ink than a black one."""
    share = MUTE_FLOOR
    while share < 0.96 and contrast(mix(ink, ground, share), ground) < MUTE_TARGET:
        share += 0.02
    return round(share, 2)


def greys(ground: str, ink: str) -> dict[str, str]:
    share = mute_share(ground, ink)
    return {
        "--mute": mix(ink, ground, share),
        "--ink-2": mix(ink, ground, min(0.96, share + 0.2)),
        "--edge": mix(ink, ground, max(0.42, share - 0.14)),
    }


def mode_of(ground: str) -> str:
    return "light" if luminance(ground) > 0.35 else "dark"


def on_accent(accent: str, ground: str, ink: str) -> str:
    """Text colour to put on an accent-filled button: whichever reads better."""
    dark = ground if luminance(ground) < luminance(ink) else ink
    light = ink if dark == ground else ground
    return dark if contrast(dark, accent) >= contrast(light, accent) else light


def check(ground: str, ink: str, accent: str, signal: str) -> list[str]:
    """Return human-readable problems; empty means the theme is usable."""
    problems = []
    for name, value in (("ground", ground), ("ink", ink), ("accent", accent), ("signal", signal)):
        if not HEX.match(value or ""):
            problems.append(f"{name} must be a colour like #1F40C4.")
    if problems:
        return problems
    pairs = [
        ("Text on the background", ink, MIN_INK),
        ("Secondary text on the background", greys(ground, ink)["--mute"], MIN_TEXT),
        ("The accent colour on the background", accent, MIN_TEXT),
        ("The signal colour on the background", signal, MIN_TEXT),
    ]
    for label, colour, minimum in pairs:
        ratio = contrast(colour, ground)
        if ratio < minimum:
            problems.append(f"{label} has contrast {ratio:.1f}:1; it needs at least {minimum:g}:1.")
    button = contrast(on_accent(accent, ground, ink), accent)
    if button < MIN_TEXT:
        problems.append(
            f"Button text on the accent colour has contrast {button:.1f}:1; it needs at least {MIN_TEXT:g}:1."
        )
    return problems


def validate(ground: str, ink: str, accent: str, signal: str):
    problems = check(ground, ink, accent, signal)
    if problems:
        raise ValidationError(problems)


def css_variables(ground: str, ink: str, accent: str, signal: str) -> dict[str, str]:
    return {
        "--ground": ground.upper(),
        "--ink": ink.upper(),
        "--accent": accent.upper(),
        "--signal": signal.upper(),
        "--on-accent": on_accent(accent, ground, ink).upper(),
        **{k: v.upper() for k, v in greys(ground, ink).items()},
    }


def preset(name: str) -> dict[str, str]:
    _, ground, ink, accent, signal = PRESETS.get(name, PRESETS[DEFAULT])
    return {"theme_ground": ground, "theme_ink": ink, "theme_accent": accent, "theme_signal": signal}
