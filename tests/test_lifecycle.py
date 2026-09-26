"""One event, end to end: create, team up, submit, deadline, assign, score,
normalize, publish. Through the API where the checker would go, through the
UI where a person would."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import ApiToken
from events.models import Event, EventRole, Project, Role, Team, TeamInvite, TeamMembership
from judging.models import JudgeAssignment

from .base import FIXTURES, SeededTestCase


class LifecycleTests(TestCase):
    def setUp(self):
        self.org = User.objects.create_user("org", "org@example.org", "pw")
        self.alice = User.objects.create_user("alice", "alice@example.org", "pw")
        self.bob = User.objects.create_user("bob", "bob@example.org", "pw")
        self.judge = User.objects.create_user("judy", "judy@example.org", "pw")
        _, self.org_token = ApiToken.issue(self.org)
        _, self.alice_token = ApiToken.issue(self.alice)

    def auth(self, raw):
        return {"HTTP_AUTHORIZATION": f"Bearer {raw}"}

    def test_full_event(self):
        now = timezone.now()
        r = self.client.post(
            "/api/events",
            data={
                "name": "Test Hack",
                "submissions_open_at": (now - timedelta(days=1)).isoformat(),
                "submissions_close_at": (now + timedelta(days=1)).isoformat(),
                "tracks": ["Tools", "Games"],
            },
            content_type="application/json",
            **self.auth(self.org_token),
        )
        self.assertEqual(r.status_code, 201, r.content)
        slug = r.json()["slug"]
        event = Event.objects.get(slug=slug)
        self.assertTrue(EventRole.objects.filter(event=event, user=self.org, role=Role.ORGANIZER).exists())
        self.assertEqual(event.tracks.count(), 2)

        # Alice forms a team and invites Bob by link.
        r = self.client.post(f"/api/events/{slug}/teams?name=Nightshift", **self.auth(self.alice_token))
        self.assertEqual(r.status_code, 201, r.content)
        team_id = r.json()["id"]
        r = self.client.post(f"/api/events/{slug}/teams/{team_id}/invites", **self.auth(self.alice_token))
        self.assertEqual(r.status_code, 200)
        token = r.json()["token"]
        self.client.force_login(self.bob)
        r = self.client.post(f"/teams/join/{token}/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(TeamMembership.objects.filter(team_id=team_id, user=self.bob).exists())
        invite = TeamInvite.objects.get(token=token)
        self.assertEqual(invite.uses, 1)
        self.client.logout()

        # Draft, edit, submit.
        track = event.tracks.first()
        r = self.client.post(
            f"/api/events/{slug}/projects",
            data={"title": "Quiet Hours", "tagline": "shh", "track_id": track.id, "tech_tags": ["django"]},
            content_type="application/json",
            **self.auth(self.alice_token),
        )
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()["id"]
        self.assertEqual(r.json()["status"], "draft")
        self.assertEqual(self.client.get(f"/events/{slug}/gallery/").status_code, 200)
        self.assertNotContains(self.client.get(f"/events/{slug}/gallery/"), "Quiet Hours")  # drafts are private
        self.assertEqual(
            self.client.get(f"/api/events/{slug}/projects/{pid}").status_code, 403
        )  # anonymous cannot see a draft
        r = self.client.patch(
            f"/api/events/{slug}/projects/{pid}",
            data={"title": "Quiet Hours v2", "submit": True},
            content_type="application/json",
            **self.auth(self.alice_token),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "submitted")
        self.assertContains(self.client.get(f"/events/{slug}/gallery/"), "Quiet Hours v2")

        # Deadline passes: edits and joins are refused, by the server.
        event.submissions_close_at = now - timedelta(minutes=1)
        event.save()
        r = self.client.patch(
            f"/api/events/{slug}/projects/{pid}",
            data={"title": "too late"},
            content_type="application/json",
            **self.auth(self.alice_token),
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Project.objects.get(pk=pid).title, "Quiet Hours v2")
        r = self.client.post(
            f"/api/events/{slug}/projects",
            data={"title": "late"},
            content_type="application/json",
            **self.auth(self.alice_token),
        )
        self.assertEqual(r.status_code, 403)
        self.client.force_login(User.objects.create_user("carol", "carol@example.org", "pw"))
        self.client.post(f"/teams/join/{token}/")
        self.assertFalse(TeamMembership.objects.filter(team_id=team_id, user__username="carol").exists())
        self.client.logout()

        # Organizer adds a judge and assigns; the judge scores; results are computed and published.
        EventRole.objects.create(event=event, user=self.judge, role=Role.JUDGE)
        r = self.client.post(
            f"/api/events/{slug}/assignments/auto",
            data={"reviews_per_project": 1},
            content_type="application/json",
            **self.auth(self.org_token),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["created"], 1)
        a = JudgeAssignment.objects.get(event=event)
        self.assertEqual(a.judge, self.judge)
        _, judge_token = ApiToken.issue(self.judge)
        r = self.client.post(
            f"/api/judges/me/assignments/{a.pk}/scores",
            data={"scores": {"functionality": 4, "quality": 5}, "comment": "nice", "submit": True},
            content_type="application/json",
            **self.auth(judge_token),
        )
        self.assertEqual(r.status_code, 400)  # innovation missing
        r = self.client.post(
            f"/api/judges/me/assignments/{a.pk}/scores",
            data={"scores": {"functionality": 4, "quality": 5, "innovation": 3}, "comment": "nice", "submit": True},
            content_type="application/json",
            **self.auth(judge_token),
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "submitted")
        r = self.client.post(f"/api/events/{slug}/results/recompute", **self.auth(self.org_token))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["reviews"], 1)
        self.assertEqual(self.client.get(f"/api/events/{slug}/results").status_code, 403)
        r = self.client.post(f"/api/events/{slug}/results/publish", **self.auth(self.org_token))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(f"/api/events/{slug}/results")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()[0]["rank_normalized"], 1)
        self.assertAlmostEqual(r.json()[0]["raw_mean"], 4.0)
        self.assertContains(self.client.get(f"/events/{slug}/results/"), "Quiet Hours v2")

        # Everything above is in the audit log.
        actions = set(event.audit_entries.values_list("action", flat=True))
        for expected in {
            "event.create",
            "team.create",
            "team.invite.create",
            "team.join",
            "project.create",
            "project.update",
            "project.submit",
            "assignment.balanced",
            "score.submit",
            "results.recompute",
            "results.publish",
        }:
            self.assertIn(expected, actions)


class SeedTests(SeededTestCase):
    def test_seed_is_idempotent(self):
        before = (User.objects.count(), Project.objects.count(), JudgeAssignment.objects.count(), Team.objects.count())
        call_command("seed_fixtures", str(FIXTURES), quiet=True)
        after = (User.objects.count(), Project.objects.count(), JudgeAssignment.objects.count(), Team.objects.count())
        self.assertEqual(before, after)

    def test_seed_shape(self):
        self.assertEqual(self.event.tracks.count(), 8)
        self.assertEqual(EventRole.objects.filter(event=self.event, role=Role.JUDGE).count(), 30)
        self.assertEqual(Team.objects.filter(event=self.event).count(), 40)
        self.assertEqual(Project.objects.filter(event=self.event).count(), 41)
        self.assertEqual(Project.objects.filter(event=self.event, duplicate_of__isnull=False).count(), 1)
        self.assertEqual(JudgeAssignment.objects.filter(event=self.event).count(), 126)
        self.assertTrue(self.event.submissions_closed())
        dup = Project.objects.get(external_id="prj_41")
        self.assertEqual(dup.duplicate_of.external_id, "prj_07")

    def test_seeded_tokens_are_reproducible(self):
        from accounts.models import ApiToken

        raw = ApiToken.deterministic_raw("jdg_a")
        self.assertEqual(ApiToken.authenticate(raw), self.judge_a)
