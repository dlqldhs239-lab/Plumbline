"""The judge console: the pile, the place in it, and the states a review
can be in. Isolation itself is tested in test_acceptance and test_review_findings."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import Client
from django.utils import timezone

from events import services as event_services
from events.models import Event, EventRole, Role
from judging import services
from judging.models import JudgeAssignment
from judging.views import _own_marks

from .base import SeededTestCase


class JudgeConsoleTests(SeededTestCase):
    def setUp(self):
        self.client.force_login(self.judge_b)
        self.mine = list(JudgeAssignment.objects.filter(judge=self.judge_b).order_by("batch", "id"))

    def test_queue_counts_and_own_marks(self):
        r = self.client.get(f"/judge/{self.event.slug}/")
        self.assertContains(r, f"All {len(self.mine)} submitted.")
        marks = r.context["marks"]
        self.assertEqual(marks["n"], len(self.mine))
        self.assertEqual(sum(b["n"] for b in marks["bins"]), len(self.mine))
        self.assertAlmostEqual(marks["mean"], 4.222, places=3)  # jdg_02 in the fixture set
        self.assertContains(r, "Your marks so far")

    def test_the_queue_holds_only_this_judges_assignments(self):
        other = JudgeAssignment.objects.exclude(judge=self.judge_b).first()
        r = self.client.get(f"/judge/{self.event.slug}/")
        ids = {i["a"].pk for i in r.context["items"]}
        self.assertEqual(ids, {a.pk for a in self.mine})
        self.assertNotIn(other.pk, ids)

    def test_no_marks_block_before_the_first_submission(self):
        rubric = services.ensure_rubric(self.event)
        self.assertIsNone(_own_marks(rubric, []))

    def test_review_page_knows_its_place(self):
        first, second = self.mine[0], self.mine[1]
        r = self.client.get(f"/judge/{self.event.slug}/review/{second.pk}/")
        self.assertEqual(r.context["pos"], 2)
        self.assertEqual(r.context["previous"].pk, first.pk)
        self.assertEqual(r.context["following"].pk, self.mine[2].pk)
        self.assertContains(r, f"Review 2 of {len(self.mine)}")
        r = self.client.get(f"/judge/{self.event.slug}/review/{first.pk}/")
        self.assertIsNone(r.context["previous"])
        self.assertContains(r, 'aria-current="step"', count=1)

    def test_a_submitted_review_is_closed_and_loads_no_script(self):
        r = self.client.get(f"/judge/{self.event.slug}/review/{self.mine[0].pk}/")
        self.assertContains(r, "A submitted review is closed")
        self.assertNotContains(r, "review.js")
        self.assertNotContains(r, 'name="submit"')
        self.assertContains(r, 'data-save=""')
        self.assertContains(r, " disabled>", count=15)  # three criteria, five steps


class ReviewStateTests(SeededTestCase):
    def make(self, **dates):
        now = timezone.now()
        defaults = {
            "submissions_open_at": now - timedelta(days=3),
            "submissions_close_at": now - timedelta(days=1),
        }
        defaults.update(dates)
        event = Event.objects.create(slug="states", name="States", **defaults)
        EventRole.objects.create(event=event, user=self.organizer, role=Role.ORGANIZER)
        team = event_services.create_team(event, self.organizer, "T")
        project = event_services.create_project(event, team, self.organizer, {"title": "Thing"})
        event_services.submit_project(project, self.organizer)
        self.judge = User.objects.create_user("jay", "jay@example.org", "pw")
        EventRole.objects.create(event=event, user=self.judge, role=Role.JUDGE)
        a = services.assign_manual(event, self.organizer, self.judge, project)
        self.client.force_login(self.judge)
        return event, a

    def test_open(self):
        event, a = self.make()
        r = self.client.get(f"/judge/{event.slug}/review/{a.pk}/")
        self.assertContains(r, "Submit and next")
        self.assertContains(r, "review.js")
        self.assertContains(r, f'data-save="/api/judges/me/assignments/{a.pk}/scores"')
        self.assertContains(self.client.get(f"/judge/{event.slug}/"), "Start with Thing")
        self.assertContains(self.client.get("/judge/"), "1 to review")

    def test_not_yet_open(self):
        event, a = self.make(judging_open_at=timezone.now() + timedelta(days=1))
        r = self.client.get(f"/judge/{event.slug}/review/{a.pk}/")
        self.assertContains(r, "Judging opens")
        self.assertNotContains(r, "Submit and next")
        self.assertContains(self.client.get(f"/judge/{event.slug}/"), "Judging opens")
        r = self.client.post(
            f"/judge/{event.slug}/review/{a.pk}/", {"score_functionality": "4", "save": "1"}, follow=True
        )
        self.assertContains(r, "Judging is not open")
        self.assertEqual(a.scores.count(), 0)

    def test_closed(self):
        event, a = self.make(
            judging_open_at=timezone.now() - timedelta(hours=5), judging_close_at=timezone.now() - timedelta(hours=1)
        )
        r = self.client.get(f"/judge/{event.slug}/review/{a.pk}/")
        self.assertContains(r, "Judging has closed")
        self.assertContains(self.client.get(f"/judge/{event.slug}/"), "Judging has closed")

    def test_the_page_saves_drafts_with_the_session_and_a_csrf_token(self):
        """What the page script does: session cookie, CSRF token, draft only."""
        event, a = self.make()
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.judge)
        page = c.get(f"/judge/{event.slug}/review/{a.pk}/")
        token = page.context["csrf_token"]
        url = f"/api/judges/me/assignments/{a.pk}/scores"
        body = {"scores": {"functionality": 4}, "comment": "so far", "submit": False}
        refused = c.post(url, data=body, content_type="application/json")
        self.assertEqual(refused.status_code, 401)
        ok = c.post(url, data=body, content_type="application/json", HTTP_X_CSRFTOKEN=str(token))
        self.assertEqual(ok.status_code, 200, ok.content)
        a.refresh_from_db()
        self.assertEqual((a.status, a.comment, a.scores.count()), ("in_progress", "so far", 1))
        page = c.get(f"/judge/{event.slug}/")
        self.assertContains(page, "1 of 3 marked")
        self.assertContains(page, "Continue with Thing")
