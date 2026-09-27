"""The organizer console: what needs attention, whether the stored results
are still true, and the panel drawn as an elevation."""

from datetime import timedelta

from django.utils import timezone

from events import services as event_services
from events.models import Project
from events.views import results_state
from judging import services, showcase
from judging.models import JudgeAssignment, JudgeCalibration

from .base import SeededTestCase


class Console(SeededTestCase):
    def setUp(self):
        self.client.force_login(self.organizer)
        self.base = f"/events/{self.event.slug}/organize/"

    def attention(self):
        return self.client.get(self.base).context["attention"]

    def texts(self):
        return " | ".join(a["text"] for a in self.attention())


class AttentionTests(Console):
    def test_seeded_event_before_any_results(self):
        text = self.texts()
        self.assertIn("no results have been computed", text)
        self.assertIn("1 submission is marked as a duplicate", text)
        short = sum(
            1
            for p in services.eligible_projects(self.event)
            if 0 < p.assignments.count() < self.event.reviews_per_project
        )
        self.assertIn(f"{short} projects are assigned fewer than 3 reviews", text)

    def test_ready_to_publish_then_nothing_about_results(self):
        services.recompute_results(self.event, self.organizer)
        self.assertIn("They are not published", self.texts())
        services.publish_results(self.event, self.organizer)
        text = self.texts()
        self.assertNotIn("not published", text)
        self.assertNotIn("computed yet", text)

    def test_a_change_after_publication_is_the_first_thing_said(self):
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)
        self.assertFalse(results_state(self.event)["stale"])
        project = Project.objects.get(external_id="prj_01")
        event_services.set_hidden(project, self.organizer, True)
        state = results_state(self.event)
        self.assertTrue(state["stale"])
        items = self.attention()
        stale = [a for a in items if "changed after the results were computed" in a["text"]]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["level"], "bad")
        self.assertIn("published", stale[0]["text"])
        page = self.client.get(self.base + "results/")
        self.assertContains(page, "Recompute to bring them up to date")
        services.recompute_results(self.event, self.organizer)
        self.assertFalse(results_state(self.event)["stale"])

    def test_a_late_review_makes_the_results_stale(self):
        services.recompute_results(self.event, self.organizer)
        a = JudgeAssignment.objects.filter(event=self.event, status="submitted").first()
        JudgeAssignment.objects.filter(pk=a.pk).update(updated_at=timezone.now() + timedelta(minutes=1))
        self.assertTrue(results_state(self.event)["stale"])

    def test_judges_who_have_not_started_are_named(self):
        a = JudgeAssignment.objects.filter(event=self.event, judge=self.judge_a).first()
        a.scores.all().delete()
        JudgeAssignment.objects.filter(pk=a.pk).update(status="pending", submitted_at=None)
        text = self.texts()
        self.assertIn("1 judge has not started", text)
        self.assertIn(self.judge_a.get_full_name(), text)
        self.assertIn("reviews are still to come", text)


class ElevationTests(Console):
    def test_nothing_to_draw_before_results(self):
        self.assertIsNone(showcase.elevation(self.event))
        page = self.client.get(self.base + "results/")
        self.assertContains(page, "Nothing computed yet")
        self.assertNotContains(page, "<svg")

    def test_geometry_comes_from_the_calibration(self):
        services.recompute_results(self.event, self.organizer)
        e = showcase.elevation(self.event)
        rows = list(JudgeCalibration.objects.filter(event=self.event))
        self.assertEqual(e["judges"], len(rows))
        self.assertEqual(e["flat"], 1)
        self.assertEqual(e["thin"], sum(1 for r in rows if r.review_count < 3))
        means = [q["mean"] for q in e["posts"]]
        self.assertEqual(means, sorted(means))
        self.assertAlmostEqual(e["range"], max(means) - min(means))
        reviews = sum(r.review_count for r in rows)
        self.assertAlmostEqual(e["datum"]["value"], sum(r.mean * r.review_count for r in rows) / reviews)
        xs = [q["x"] for q in e["posts"]]
        self.assertEqual(xs, sorted(xs))
        self.assertGreaterEqual(min(xs), e["left"])
        self.assertLessEqual(max(xs), e["right"])
        for q in e["posts"]:
            self.assertGreaterEqual(q["y"], 0)
            self.assertLessEqual(q["y"], e["height"])
            if q["lean"] > 0:
                self.assertLess(q["y"], e["datum"]["y"])  # a generous judge stands above the line
            elif q["lean"] < 0:
                self.assertGreater(q["y"], e["datum"]["y"])
        flat = next(q for q in e["posts"] if q["flat"])
        self.assertEqual(flat["label"], "07")
        self.assertIn("no opinion", flat["tip"])

    def test_results_page_draws_it_and_lists_the_same_figures(self):
        services.recompute_results(self.event, self.organizer)
        page = self.client.get(self.base + "results/")
        html = page.content.decode()
        self.assertEqual(html.count('class="bob"'), 30)
        self.assertContains(page, "every mark the same")
        self.assertContains(page, "How each judge marks")
        self.assertContains(page, "Largest moves")
        self.assertContains(page, "Small Relay")

    def test_only_organizers(self):
        services.recompute_results(self.event, self.organizer)
        for user in (self.judge_a, self.participant):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.base + "results/").status_code, 403)


class SettingsFormTests(Console):
    def test_the_form_is_in_parts_and_holds_every_field(self):
        page = self.client.get(self.base + "settings/")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertEqual(html.count("<fieldset"), 5)
        for name in page.context["form"].fields:
            self.assertIn(f'name="{name}"', html, name)

    def test_create_page_renders(self):
        page = self.client.get("/events/new/")
        self.assertContains(page, "Create an event")
        self.assertContains(page, "You become the organizer")

    def test_errors_are_shown_beside_the_field(self):
        page = self.client.post("/events/new/", {"name": "", "submissions_open_at": "nope"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Some fields need another look")
        self.assertContains(page, "field invalid")
