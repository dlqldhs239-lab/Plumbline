"""Event themes: four colours in, contrast checked, nothing unreadable saved."""

from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.utils import timezone

from events import theme
from events.models import Event

from .base import SeededTestCase

CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "plumbline.css"


class ContrastTests(SimpleTestCase):
    def test_known_ratios(self):
        self.assertAlmostEqual(theme.contrast("#000000", "#FFFFFF"), 21.0, places=1)
        self.assertAlmostEqual(theme.contrast("#FFFFFF", "#FFFFFF"), 1.0, places=2)
        self.assertAlmostEqual(theme.contrast("#777777", "#FFFFFF"), 4.48, places=1)

    def test_every_preset_passes(self):
        for key, (_, ground, ink, accent, signal) in theme.PRESETS.items():
            self.assertEqual(theme.check(ground, ink, accent, signal), [], key)

    def test_unreadable_themes_are_refused(self):
        problems = theme.check("#FFFFFF", "#EEEEEE", "#FFFF00", "#FFCCCC")
        self.assertEqual(len(problems), 5)
        self.assertIn("needs at least", problems[0])
        with self.assertRaises(ValidationError):
            theme.validate("#101010", "#151515", "#00E5D0", "#FF3D6E")

    def test_malformed_colours_are_refused(self):
        for bad in ("red", "#12345", "#GGGGGG", "", "url(x)", "#fff;}body{display:none"):
            self.assertTrue(theme.check(bad, "#FFFFFF", "#00E5D0", "#FF3D6E"), bad)

    def test_button_text_picks_the_readable_side(self):
        self.assertEqual(theme.on_accent("#00E5D0", "#080C18", "#E9EEFF"), "#080C18")
        self.assertEqual(theme.on_accent("#1F40C4", "#E9ECEA", "#10161B"), "#E9ECEA")

    def test_mode_follows_the_ground(self):
        self.assertEqual(theme.mode_of("#080C18"), "dark")
        self.assertEqual(theme.mode_of("#F4F1EA"), "light")


class StylesheetRuleTests(SimpleTestCase):
    """The design rules that a grep can hold."""

    def setUp(self):
        self.css = CSS.read_text(encoding="utf-8")

    def test_no_banned_patterns(self):
        for banned in (
            "transition: all",
            "box-shadow",
            "backdrop-filter",
            "linear-gradient(to ",
            "radial-gradient(circle, #",
            "border-radius: 8px",
            "border-radius: 12px",
            "Inter",
            "Roboto",
            "system-ui",
        ):
            self.assertNotIn(banned, self.css, banned)

    def test_colours_only_in_token_blocks(self):
        import re

        body = re.sub(r":root(\[data-mode=\"light\"\])? \{.*?\n\}", "", self.css, flags=re.S)
        body = re.sub(r"@media print \{.*", "", body, flags=re.S)
        stray = re.findall(r"#[0-9A-Fa-f]{3,6}\b", body)
        self.assertEqual([c for c in stray if c.lower() not in ("#000", "#fff")], [])

    def test_no_network_in_templates_or_css(self):
        import re

        root = CSS.parent.parent.parent
        offenders = []
        for path in list((root / "templates").rglob("*.html")) + [CSS]:
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"""(?:src|href)=["'](https?://[^"']+)|url\(["']?(https?://[^)"']+)""", text):
                offenders.append((path.name, m.group(0)))
        self.assertEqual(offenders, [])


class EventThemeTests(SeededTestCase):
    def test_event_pages_carry_their_theme(self):
        r = self.client.get(f"/events/{self.event.slug}/")
        self.assertContains(r, 'data-mode="dark"')
        self.assertContains(r, "--accent:#00E5D0")
        self.event.theme_ground, self.event.theme_ink = "#F4F1EA", "#14171C"
        self.event.theme_accent, self.event.theme_signal = "#7A2323", "#A3341F"
        self.event.full_clean()
        self.event.save()
        r = self.client.get(f"/events/{self.event.slug}/gallery/")
        self.assertContains(r, 'data-mode="light"')
        self.assertContains(r, "--ground:#F4F1EA")
        self.assertContains(r, "--on-accent:#F4F1EA")
        self.assertNotContains(self.client.get("/"), "--ground:#F4F1EA")

    def test_settings_form_applies_a_preset_and_refuses_bad_colours(self):
        self.client.force_login(self.organizer)
        url = f"/events/{self.event.slug}/organize/settings/"
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        data = {k: v for k, v in page.context["form"].initial.items() if v is not None}
        data.update(
            {
                "name": self.event.name,
                "slug": self.event.slug,
                "reviews_per_project": 3,
                "voting_access": "closed",
                "voting_credits": 0,
            }
        )
        for key in ("submissions_open_at", "submissions_close_at", "judging_open_at"):
            data[key] = getattr(self.event, key).strftime("%Y-%m-%dT%H:%M")
        for key in ("judging_close_at", "voting_open_at", "voting_close_at", "created_by"):
            data.pop(key, None)
        ok = dict(data, theme_preset="oxblood")
        r = self.client.post(url, ok)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme_accent, "#7A2323")
        bad = dict(
            data,
            theme_preset="",
            theme_ground="#FFFFFF",
            theme_ink="#F0F0F0",
            theme_accent="#FFFF00",
            theme_signal="#FFDDDD",
        )
        r = self.client.post(url, bad)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "needs at least")
        self.event.refresh_from_db()
        self.assertEqual(self.event.theme_ground, "#F4F1EA")

    def test_api_refuses_unreadable_theme(self):
        r = self.client.patch(
            f"/api/events/{self.event.slug}",
            data={"theme_ground": "#FFFFFF", "theme_ink": "#FAFAFA"},
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("contrast", r.json()["detail"])

    def test_new_events_default_to_a_valid_theme(self):
        now = timezone.now()
        e = Event(slug="t", name="T", submissions_open_at=now, submissions_close_at=now + timedelta(days=1))
        e.full_clean()
        self.assertEqual(e.theme_mode, "dark")

    def test_styleguide_renders_every_preset(self):
        self.assertEqual(self.client.get("/design/").status_code, 200)
        for key in theme.PRESETS:
            r = self.client.get(f"/design/?theme={key}")
            self.assertEqual(r.status_code, 200, key)
            self.assertContains(r, f"--ground:{theme.PRESETS[key][1].upper()}")
