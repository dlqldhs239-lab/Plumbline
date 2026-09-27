"""Bugs found by the second independent review, on 2026-09-27, in the code
written after the first. Each test is named for what could go wrong."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from accounts.models import ApiToken
from events import services as event_services
from events.models import Event, EventRole, Project, Role
from events.views import results_state
from judging import services, showcase
from judging.models import JudgeAssignment, JudgeCalibration, ProjectResult
from judging.normalization import Review, normalize

from .base import SeededTestCase


class Open(SeededTestCase):
    """An event in judging, one project, one judge with one assignment."""

    def setUp(self):
        now = timezone.now()
        self.ev = Event.objects.create(
            slug="two",
            name="Two",
            submissions_open_at=now - timedelta(days=3),
            submissions_close_at=now - timedelta(days=1),
        )
        EventRole.objects.create(event=self.ev, user=self.organizer, role=Role.ORGANIZER)
        team = event_services.create_team(self.ev, self.organizer, "T")
        self.project = event_services.create_project(self.ev, team, self.organizer, {"title": "Thing"})
        event_services.submit_project(self.project, self.organizer)
        self.judge = User.objects.create_user("jay", "jay@example.org", "pw")
        EventRole.objects.create(event=self.ev, user=self.judge, role=Role.JUDGE)
        self.a = services.assign_manual(self.ev, self.organizer, self.judge, self.project)
        _, self.token = ApiToken.issue(self.judge)
        self.full = {"functionality": 4, "quality": 4, "innovation": 4}

    def post(self, body):
        return self.client.post(
            f"/api/judges/me/assignments/{self.a.pk}/scores",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )


class SubmittedStaysSubmittedTests(Open):
    def test_a_draft_that_arrives_after_the_submit_changes_nothing(self):
        """The page saves drafts by itself. One that was read before the
        submit and written after it used to reopen the review."""
        stale = JudgeAssignment.objects.get(pk=self.a.pk)  # what the late request holds
        services.save_scores(self.a, self.judge, self.full, "final words", submit=True)
        with self.assertRaises(ValidationError):
            services.save_scores(stale, self.judge, {"functionality": 1, "quality": 1}, "older text", submit=False)
        a = JudgeAssignment.objects.get(pk=self.a.pk)
        self.assertEqual((a.status, a.comment), ("submitted", "final words"))
        self.assertIsNotNone(a.submitted_at)
        self.assertEqual(sorted(a.scores.values_list("value", flat=True)), [4, 4, 4])
        self.assertEqual(self.post({"scores": {"functionality": 1}, "submit": False}).status_code, 400)

    def test_an_organizer_who_judges_cannot_reopen_their_own_review_either(self):
        EventRole.objects.create(event=self.ev, user=self.judge, role=Role.ORGANIZER)
        services.save_scores(self.a, self.judge, self.full, "final", submit=True)
        with self.assertRaises(ValidationError):
            services.save_scores(self.a, self.judge, {"functionality": 1}, "", submit=False)
        self.client.force_login(self.judge)
        page = f"/judge/{self.ev.slug}/review/{self.a.pk}/"
        self.client.post(page, {"score_functionality": "1", "save": "1"})
        self.assertEqual(sorted(self.a.scores.values_list("value", flat=True)), [4, 4, 4])
        self.assertContains(self.client.get(page), "A submitted review is closed")

    def test_scores_without_a_comment_leave_the_comment_alone(self):
        services.save_scores(self.a, self.judge, {"functionality": 3}, "keep me", submit=False)
        self.assertEqual(self.post({"scores": {"quality": 5}}).status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.comment, "keep me")
        self.assertEqual(self.post({"scores": {}, "comment": ""}).status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual(self.a.comment, "")  # an empty comment that was sent is a cleared comment

    def test_a_refused_submit_keeps_what_was_entered(self):
        self.client.force_login(self.judge)
        page = f"/judge/{self.ev.slug}/review/{self.a.pk}/"
        r = self.client.post(
            page, {"score_functionality": "5", "score_quality": "4", "comment": "half done", "submit": "1"}, follow=True
        )
        self.assertContains(r, "Every criterion needs a score")
        self.assertContains(r, "saved as a draft")
        self.a.refresh_from_db()
        self.assertEqual((self.a.status, self.a.comment, self.a.scores.count()), ("in_progress", "half done", 2))
        self.assertContains(r, 'value="5" checked')
        self.assertContains(r, "half done")


class AddressTests(Open):
    def test_only_web_addresses_reach_the_public_page(self):
        bad = ("javascript:alert(document.cookie)", "data:text/html,x", "ftp://example.org/a", "//example.org", "x")
        for value in bad:
            for field in ("image_urls", "thumbnail_url", "repo_url", "live_url", "demo_video_url"):
                data = {field: [value] if field == "image_urls" else value}
                with self.assertRaises(ValidationError, msg=(field, value)):
                    event_services.update_project(self.project, self.organizer, data)
        _, token = ApiToken.issue(self.organizer)
        r = self.client.patch(
            f"/api/events/{self.ev.slug}/projects/{self.project.pk}",
            data={"title": "Thing", "image_urls": ["javascript:alert(1)"]},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(r.status_code, 400)
        self.project.refresh_from_db()
        self.assertEqual(self.project.image_urls, [])
        ok = {"image_urls": ["https://example.org/a.png", "http://example.org/b.png"], "repo_url": "https://e.org/r"}
        event_services.update_project(self.project, self.organizer, ok)
        page = self.client.get(self.project.get_absolute_url()).content.decode()
        self.assertNotIn("javascript:", page)
        self.assertIn("https://example.org/a.png", page)

    def test_gallery_and_tags_have_a_size(self):
        with self.assertRaises(ValidationError):
            event_services.update_project(
                self.project, self.organizer, {"image_urls": [f"https://e.org/{i}.png" for i in range(13)]}
            )
        with self.assertRaises(ValidationError):
            event_services.update_project(self.project, self.organizer, {"tech_tags": ["t" * 41]})


class HomeCountTests(SeededTestCase):
    def test_the_project_count_is_the_same_whoever_looks(self):
        counts = set()
        for user in (None, self.participant, self.judge_a, self.organizer, self.admin):
            if user:
                self.client.force_login(user)
            events = {e.slug: e.project_count for e in self.client.get("/").context["events"]}
            counts.add(events[self.event.slug])
        self.assertEqual(counts, {40})


class StaleSettingTests(SeededTestCase):
    def test_changing_the_adjustment_makes_the_results_stale_and_the_page_says_what_was_used(self):
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)
        self.assertEqual(services.computed_jury_k(self.event), 3.0)
        services.set_jury_k(services.ensure_rubric(self.event), self.organizer, 0)
        state = results_state(self.event)
        self.assertTrue(state["stale"])
        self.assertTrue(state["setting_changed"])
        page = self.client.get(f"/events/{self.event.slug}/results/")
        self.assertContains(page, "as if 3 more reviews")
        self.assertNotContains(page, "switched the jury-size adjustment off")
        self.client.force_login(self.organizer)
        self.assertContains(
            self.client.get(f"/events/{self.event.slug}/organize/results/"),
            "The jury-size adjustment was changed after these results were computed with 3",
        )
        services.recompute_results(self.event, self.organizer)
        self.assertFalse(results_state(self.event)["stale"])
        self.assertContains(
            self.client.get(f"/events/{self.event.slug}/results/"), "switched the jury-size adjustment off"
        )

    def test_reviews_per_project_moves_the_default_and_is_noticed(self):
        services.recompute_results(self.event, self.organizer)
        event_services.update_event(self.event, self.organizer, {"reviews_per_project": 5})
        self.assertTrue(results_state(self.event)["setting_changed"])


class PlacesTests(SeededTestCase):
    def setUp(self):
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)

    def test_hiding_the_leader_leaves_no_gap(self):
        first = ProjectResult.objects.get(event=self.event, rank=1)
        last = ProjectResult.objects.filter(event=self.event).order_by("-rank").first()
        event_services.set_hidden(first.project, self.organizer, True)
        rows = services.placed(self.event)
        self.assertEqual(len(rows), 39)
        self.assertEqual([r.place for r in rows][:4], [1, 2, 3, 4])
        self.assertEqual(max(r.place for r in rows), 39)
        self.assertTrue(all(r.of == 39 for r in rows))
        page = self.client.get(f"/events/{self.event.slug}/results/")
        self.assertEqual([r.place for r in page.context["leaders"]], [1, 2, 3])
        self.assertContains(self.client.get(last.project.get_absolute_url()), "<strong>39</strong> of 39")
        case = showcase.case(self.event)
        self.assertEqual(case["projects"], 39)
        self.assertLessEqual(case["to_rank"], 39)
        self.assertLessEqual(case["from_rank"], 39)

    def test_ties_share_a_place(self):
        a, b = list(ProjectResult.objects.filter(event=self.event).order_by("rank")[:2])
        ProjectResult.objects.filter(pk=b.pk).update(rank=a.rank)
        places = [r.place for r in services.placed(self.event)][:3]
        self.assertEqual(places, [1, 1, 3])

    def test_a_track_that_is_not_this_events_shows_nothing(self):
        Project.objects.filter(event=self.event).order_by("id")[:3]
        first_three = list(Project.objects.filter(event=self.event).values_list("pk", flat=True)[:3])
        Project.objects.filter(pk__in=first_three).update(track=None)
        services.recompute_results(self.event, self.organizer)
        for bad in ("abc", "0", "-1", "9" * 30, "999999"):
            r = self.client.get(f"/events/{self.event.slug}/results/?track={bad}")
            self.assertEqual(r.status_code, 200, bad)
            self.assertEqual(r.context["rows"], [], bad)
            self.assertContains(r, "No results yet")


class FlatWordingTests(SeededTestCase):
    def test_equal_totals_are_not_called_equal_marks(self):
        reviews = [Review("F", f"p{i}", 3.0) for i in range(4)] + [Review("A", "p0", 5.0), Review("A", "p1", 1.0)]
        self.assertTrue(normalize(reviews, 1, 5).judges["F"].flat)  # flat is about the totals
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)
        self.assertEqual(showcase.case(self.event)["kind"], "flat")
        flat = JudgeCalibration.objects.get(event=self.event, flat=True)
        score = JudgeAssignment.objects.filter(judge_id=flat.judge_id).first().scores.first()
        # Same totals, different marks: 5 here, and 3 on another criterion of the same review.
        other = score.assignment.scores.exclude(pk=score.pk).first()
        score.value, other.value = 5, 3
        score.save()
        other.save()
        services.recompute_results(self.event, self.organizer)
        self.assertTrue(JudgeCalibration.objects.get(event=self.event, judge_id=flat.judge_id).flat)
        self.assertEqual(showcase.case(self.event)["kind"], "mover")
        home = self.client.get("/")
        self.assertNotContains(home, "One judge gave everyone")
        self.assertContains(self.client.get(f"/events/{self.event.slug}/results/"), "gave every project the same score")


class DatesTests(SeededTestCase):
    def test_judging_cannot_close_before_it_opens(self):
        now = timezone.now()
        base = {"name": "Dates", "submissions_open_at": now, "submissions_close_at": now + timedelta(days=30)}
        with self.assertRaisesMessage(ValidationError, "when submissions close"):
            event_services.create_event(self.organizer, {**base, "judging_close_at": now + timedelta(days=1)})
        with self.assertRaises(ValidationError):
            event_services.create_event(
                self.organizer,
                {**base, "judging_open_at": now + timedelta(days=40), "judging_close_at": now + timedelta(days=35)},
            )
        event_services.create_event(self.organizer, {**base, "judging_close_at": now + timedelta(days=45)})


class BrokenCalibrationTests(SeededTestCase):
    def test_rows_without_a_mean_or_without_reviews_do_not_take_the_pages_down(self):
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)
        row = JudgeCalibration.objects.filter(event=self.event).first()
        JudgeCalibration.objects.filter(pk=row.pk).update(mean=None)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(len(showcase.panel(self.event)["judges"]), 29)
        JudgeCalibration.objects.filter(event=self.event).update(review_count=0)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertNotContains(self.client.get("/"), "field-data")
        self.client.force_login(self.organizer)
        self.assertEqual(self.client.get(f"/events/{self.event.slug}/organize/results/").status_code, 200)
        self.assertIsNone(showcase.elevation(self.event))


class PublishTests(SeededTestCase):
    def test_nothing_is_published_before_anything_is_computed(self):
        with self.assertRaises(ValidationError):
            services.publish_results(self.event, self.organizer)
        self.client.force_login(self.organizer)
        r = self.client.post(f"/events/{self.event.slug}/organize/results/", {"action": "publish"}, follow=True)
        self.assertContains(r, "nothing to publish yet")
        r = self.client.post(f"/api/events/{self.event.slug}/results/publish", **self.bearer("organizer"))
        self.assertEqual(r.status_code, 400)
        self.event.refresh_from_db()
        self.assertFalse(self.event.results_published)
        services.publish_results(self.event, self.organizer, False)  # unpublishing needs no results


class SampleSecretsTests(SeededTestCase):
    def test_staff_are_told_and_nobody_else(self):
        text = "runs on the secret key"
        with override_settings(PLUMBLINE_SAMPLE_SECRETS=True):
            self.assertNotContains(self.client.get("/"), text)
            self.client.force_login(self.participant)
            self.assertNotContains(self.client.get("/dashboard/"), text)
            self.client.force_login(self.organizer)
            self.assertContains(self.client.get("/dashboard/"), text)
        with override_settings(PLUMBLINE_SAMPLE_SECRETS=False):
            self.assertNotContains(self.client.get("/dashboard/"), text)
