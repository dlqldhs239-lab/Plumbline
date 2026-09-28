"""What a keyboard or a screen reader needs from the markup. The browser side is tools/check_screens.py."""

import re
from pathlib import Path

from django.conf import settings

from .base import SeededTestCase

TEMPLATES = Path(settings.BASE_DIR) / "templates"


class ReachableTests(SeededTestCase):
    def test_every_rubric_field_has_a_name(self):
        self.client.force_login(self.organizer)
        html = self.client.get("/events/sample-hack-2026/organize/rubric/").content.decode()
        fields = re.findall(r'<input[^>]*name="c-\d+-(?:key|name|weight|description)"[^>]*>', html)
        self.assertGreaterEqual(len(fields), 4)
        for field in fields:
            self.assertIn("aria-label=", field)

    def test_the_page_can_be_skipped_to(self):
        html = self.client.get("/").content.decode()
        self.assertIn('<a class="skip" href="#main">', html)
        self.assertIn('<main id="main"', html)
        self.assertLess(html.index('class="skip"'), html.index("<header"))

    def test_no_column_head_is_empty_and_no_heading_skips_a_level(self):
        for page in TEMPLATES.rglob("*.html"):
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("<th></th>", text, page.name)
            self.assertNotRegex(text, r"<h[3-6][\s>{]", page.name)
