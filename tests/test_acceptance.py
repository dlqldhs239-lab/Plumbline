"""The seven checker requests, as unit tests, plus the probes the checker
does not make but a judge with curl would."""

from django.contrib.auth.models import User

from events.models import EventRole, Role

from .base import SeededTestCase


class AcceptanceChecksTests(SeededTestCase):
    def test_t1_gallery_is_public_and_shows_fixture_projects(self):
        r = self.client.get(f"/events/{self.event.slug}/gallery/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Glass Signal")

    def test_t1_closed_event_refuses_submission(self):
        r = self.client.post(
            f"/api/events/{self.event.slug}/projects",
            data={"title": "dogfood-late-submission-probe", "summary": "probe"},
            content_type="application/json",
            **self.bearer("participant"),
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("closed", r.json()["detail"].lower())

    def test_t2_judge_sees_own_scores(self):
        r = self.client.get("/api/judges/me/scores", **self.bearer("judge_a"))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["judge"], self.judge_a.username)
        self.assertTrue(body["assignments"])
        self.assertTrue(all(a["scores"] for a in body["assignments"]))

    def test_t2_judge_cannot_see_peer_scores(self):
        for ref in ("jdg_01", str(self.judge_a.pk), self.judge_a.username):
            r = self.client.get(f"/api/judges/{ref}/scores", **self.bearer("judge_b"))
            self.assertEqual(r.status_code, 403, ref)
            self.assertNotIn("assignments", r.json())

    def test_t2_participant_blocked_from_judge_scores(self):
        r = self.client.get("/api/judges/me/scores", **self.bearer("participant"))
        self.assertEqual(r.status_code, 403)

    def test_t2_csv_export_for_organizer(self):
        r = self.client.get(f"/api/events/{self.event.slug}/export/scores.csv", **self.bearer("organizer"))
        self.assertEqual(r.status_code, 200)
        first = r.content.decode().splitlines()[0]
        self.assertIn(",", first)
        self.assertIn("functionality", first)


class RoleIsolationTests(SeededTestCase):
    def test_anonymous_and_bad_tokens_are_401(self):
        self.assertEqual(self.client.get("/api/judges/me/scores").status_code, 401)
        self.assertEqual(self.client.get("/api/judges/me/scores", HTTP_AUTHORIZATION="Bearer nope").status_code, 401)

    def test_participant_cannot_export(self):
        r = self.client.get(f"/api/events/{self.event.slug}/export/scores.csv", **self.bearer("participant"))
        self.assertEqual(r.status_code, 403)

    def test_judge_cannot_export_or_see_progress(self):
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/export/scores.csv", **self.bearer("judge_a")).status_code, 403)
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/progress", **self.bearer("judge_a")).status_code, 403)

    def test_organizer_can_read_a_judge_within_their_event(self):
        r = self.client.get(f"/api/judges/jdg_01/scores?event={self.event.slug}", **self.bearer("organizer"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["judge"], self.judge_a.username)

    def test_organizer_of_another_event_cannot_read_this_judge(self):
        other = User.objects.create_user("other-org", "other@example.org", "x")
        from events.models import Event

        ev = Event.objects.create(
            slug="other", name="Other", submissions_open_at=self.event.submissions_open_at, submissions_close_at=self.event.submissions_close_at
        )
        EventRole.objects.create(event=ev, user=other, role=Role.ORGANIZER)
        from accounts.models import ApiToken

        _, raw = ApiToken.issue(other)
        r = self.client.get(f"/api/judges/jdg_01/scores?event={self.event.slug}", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/api/judges/jdg_01/scores", HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["assignments"], [])  # nothing from an event they do not organize

    def test_judge_cannot_load_or_score_another_judges_assignment(self):
        from judging.models import JudgeAssignment

        a = JudgeAssignment.objects.filter(judge=self.judge_a).first()
        self.assertEqual(self.client.get(f"/api/judges/me/assignments/{a.pk}", **self.bearer("judge_b")).status_code, 403)
        r = self.client.post(
            f"/api/judges/me/assignments/{a.pk}/scores",
            data={"scores": {"functionality": 5}},
            content_type="application/json",
            **self.bearer("judge_b"),
        )
        self.assertEqual(r.status_code, 403)
        # And through the UI
        self.client.force_login(self.judge_b)
        self.assertEqual(self.client.get(f"/judge/{self.event.slug}/review/{a.pk}/").status_code, 403)

    def test_judge_list_assignments_only_returns_own(self):
        r = self.client.get(f"/api/events/{self.event.slug}/assignments", **self.bearer("judge_b"))
        self.assertEqual(r.status_code, 200)
        from judging.models import JudgeAssignment

        own = set(JudgeAssignment.objects.filter(judge=self.judge_b).values_list("id", flat=True))
        self.assertEqual({a["id"] for a in r.json()}, own)

    def test_results_hidden_until_published(self):
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/results").status_code, 403)
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/results", **self.bearer("judge_a")).status_code, 403)
        self.assertEqual(self.client.get(f"/events/{self.event.slug}/results/").status_code, 403)
        self.assertEqual(self.client.get(f"/api/events/{self.event.slug}/results", **self.bearer("organizer")).status_code, 200)

    def test_session_api_write_requires_csrf(self):
        from django.test import Client

        c = Client(enforce_csrf_checks=True)
        c.force_login(self.organizer)
        r = c.post(f"/api/events/{self.event.slug}/results/recompute")
        self.assertEqual(r.status_code, 401)
        # Bearer tokens are not subject to CSRF.
        r = self.client.post(f"/api/events/{self.event.slug}/results/recompute", **self.bearer("organizer"))
        self.assertEqual(r.status_code, 200)
