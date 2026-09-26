"""Shared fixtures for the test suite.

Tests seed the organizer's real fixtures.json so they exercise the same data
the acceptance checker sees: 41 projects, 30 judges, one duplicate, one flat
judge, uneven review counts.
"""

from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from accounts.models import ApiToken
from events.models import Event, EventRole, Role

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures.json"


class SeededTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_fixtures", str(FIXTURES), quiet=True)
        cls.event = Event.objects.get(external_id="evt_01")
        cls.organizer = User.objects.get(email="organizer@example.org")
        cls.admin = User.objects.get(email="admin@example.org")
        judge_roles = EventRole.objects.filter(event=cls.event, role=Role.JUDGE).order_by("external_id")
        cls.judge_a = judge_roles[0].user  # jdg_01
        cls.judge_b = judge_roles[1].user  # jdg_02
        cls.participant = User.objects.get(email="priya1@example.org")
        cls.tokens = {}
        for name, user in {
            "organizer": cls.organizer,
            "judge_a": cls.judge_a,
            "judge_b": cls.judge_b,
            "participant": cls.participant,
            "admin": cls.admin,
        }.items():
            _, raw = ApiToken.issue(user, label=f"test:{name}")
            cls.tokens[name] = raw

    def bearer(self, who: str) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {self.tokens[who]}"}
