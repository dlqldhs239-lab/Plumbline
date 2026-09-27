"""The public pages that show judging: landing, results, event, project.
What they say comes from the data, and only from published data."""

from io import StringIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase

from events.models import Event, Project
from events.views import timeline
from judging import services, showcase
from judging.models import JudgeCalibration, ProjectResult

from .base import FIXTURES, SeededTestCase

STATIC = Path(__file__).resolve().parent.parent / "static"


class WordsTests(SimpleTestCase):
    def test_numbers_and_places_as_words(self):
        self.assertEqual(showcase.in_words(30), "thirty")
        self.assertEqual(showcase.in_words(21), "twenty-one")
        self.assertEqual(showcase.in_words(4), "four")
        self.assertEqual(showcase.in_words(140), "140")
        self.assertEqual(showcase.ordinal(12), "twelfth")
        self.assertEqual(showcase.ordinal(26), "twenty-sixth")
        self.assertEqual(showcase.ordinal(40), "fortieth")
        self.assertEqual(showcase.ordinal(101), "101st")
        self.assertEqual(showcase.ordinal(112), "112th")
        self.assertEqual(showcase.ordinal(123), "123rd")


class Published(SeededTestCase):
    def setUp(self):
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)
        self.event.refresh_from_db()


class LandingTests(SeededTestCase):
    def test_nothing_is_shown_about_judging_before_publication(self):
        services.recompute_results(self.event, self.organizer)
        self.assertIsNone(showcase.featured_event())
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "field-data")
        self.assertNotContains(r, "Small Relay")
        self.assertContains(r, "Nothing is published here yet")
        self.assertContains(r, self.event.name)  # the event itself is still listed

    def test_unlisted_events_are_never_featured(self):
        services.recompute_results(self.event, self.organizer)
        services.publish_results(self.event, self.organizer)
        Event.objects.filter(pk=self.event.pk).update(is_listed=False)
        self.assertIsNone(showcase.featured_event())
        self.assertNotContains(self.client.get("/"), "field-data")


class LandingCaseTests(Published):
    def test_the_case_is_the_flat_judge_and_every_number_is_from_the_data(self):
        case = showcase.case(self.event)
        flat = JudgeCalibration.objects.get(event=self.event, flat=True)
        self.assertEqual(case["kind"], "flat")
        self.assertEqual(case["mark"], 4)
        self.assertEqual(case["flat_reviews"], flat.review_count)
        self.assertEqual(case["judges"], 30)
        self.assertEqual(case["projects"], 40)
        rows = ProjectResult.objects.filter(event=self.event)
        self.assertEqual(case["moved"], sum(1 for r in rows if r.rank != r.rank_raw))
        r = case["project"]
        self.assertEqual(r.project.title, "Small Relay")
        self.assertEqual((case["from_rank"], case["to_rank"]), (r.rank_raw, r.rank))
        page = self.client.get("/")
        self.assertContains(page, "One judge gave everyone")
        self.assertContains(page, "a four.")
        self.assertContains(page, f"from {showcase.ordinal(r.rank_raw)} to {showcase.ordinal(r.rank)}")
        self.assertContains(page, f"{case['moved']} of 40 projects changed place.")
        for value in (r.raw_mean, r.normalized_mean, r.adjusted_mean):
            self.assertContains(page, f"{value:.3f}")

    def test_without_a_flat_judge_the_case_is_the_largest_move(self):
        JudgeCalibration.objects.filter(event=self.event).update(flat=False)
        case = showcase.case(self.event)
        self.assertEqual(case["kind"], "mover")
        rows = list(ProjectResult.objects.filter(event=self.event))
        widest = max(abs(r.rank_raw - r.rank) for r in rows)
        self.assertEqual(abs(case["from_rank"] - case["to_rank"]), widest)
        page = self.client.get("/")
        self.assertContains(page, "Thirty judges.")

    def test_the_panel_carries_no_names_and_no_ids(self):
        panel = showcase.panel(self.event)
        self.assertEqual(len(panel["judges"]), 30)
        self.assertEqual(set(panel["judges"][0]), {"n", "lean", "mean", "spread", "flat"})
        self.assertEqual(sum(1 for j in panel["judges"] if j["flat"]), 1)
        self.assertAlmostEqual(sum(j["lean"] * j["n"] for j in panel["judges"]), 0, places=1)
        html = self.client.get("/").content.decode()
        data = html.split('id="field-data"', 1)[1].split("</script>", 1)[0]
        for user in User.objects.filter(event_roles__role="judge"):
            self.assertNotIn(user.username, data)
            self.assertNotIn(user.email, data)
        self.assertNotIn("jdg_", data)

    def test_a_hidden_project_is_not_the_story(self):
        r = showcase.case(self.event)["project"]
        from events.services import set_hidden

        set_hidden(r.project, self.organizer, True)
        self.assertNotEqual(showcase.case(self.event)["project"].project_id, r.project_id)
        self.assertNotContains(self.client.get("/"), "Small Relay")


class ResultsPageTests(Published):
    def test_the_host_format_and_the_three_scores(self):
        page = self.client.get(f"/events/{self.event.slug}/results/")
        first = ProjectResult.objects.get(event=self.event, rank=1)
        self.assertContains(page, f"{first.adjusted_mean:.3f}")
        self.assertContains(page, f"adjusted / 5.00 · {first.review_count} judges")
        self.assertContains(page, "How the score is made")
        self.assertContains(page, "between 2 and 5 reviews")
        self.assertContains(page, "1 judge gave every project the same score")
        html = page.content.decode()
        self.assertEqual(html.count("<path "), 40)
        self.assertEqual(html.count('class="leader"'), 3)

    def test_slopegraph_geometry(self):
        rows = list(services.standings(self.event))
        g = showcase.slopegraph(rows)
        self.assertEqual((g["shown"], g["total"], g["height"]), (40, 40, 40 * showcase.SLOPE_ROW))
        self.assertEqual([r.rank for r in g["right"]], sorted(r.rank for r in g["right"]))
        raw = [round(r.raw_mean, 6) for r in g["left"]]
        self.assertEqual(raw, sorted(raw, reverse=True))
        by_id = {line["id"]: line for line in g["lines"]}
        relay = next(r for r in rows if r.project.title == "Small Relay")
        self.assertEqual(by_id[relay.project_id]["tone"], "down")
        top = g["right"][0]
        self.assertTrue(by_id[top.project_id]["d"].endswith(f"{g['width']} {showcase.SLOPE_ROW / 2:g}"))
        self.assertIsNone(showcase.slopegraph(rows[:1]))
        self.assertEqual(showcase.slopegraph(rows, limit=10)["shown"], 10)

    def test_track_filter_counts_places_within_the_track(self):
        track = self.event.tracks.get(external_id="trk_04")
        page = self.client.get(f"/events/{self.event.slug}/results/?track={track.pk}")
        rows = page.context["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(r.project.track_id == track.pk for r in rows))
        self.assertEqual([r.place for r in rows], list(range(1, len(rows) + 1)))
        self.assertContains(page, track.name)
        for bad in ("abc", "9" * 30, "0"):
            r = self.client.get(f"/events/{self.event.slug}/results/?track={bad}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.context["rows"], [])

    def test_api_carries_the_same_numbers(self):
        rows = self.client.get(f"/api/events/{self.event.slug}/results").json()
        first = rows[0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(first["scale_max"], 5)
        self.assertEqual([c["key"] for c in first["criterion_means"]], ["functionality", "quality", "innovation"])
        stored = ProjectResult.objects.get(event=self.event, rank=1)
        self.assertAlmostEqual(first["adjusted_mean"], stored.adjusted_mean)
        self.assertEqual([r["rank"] for r in rows], sorted(r["rank"] for r in rows))


class EventAndProjectPageTests(Published):
    def test_event_page(self):
        page = self.client.get(f"/events/{self.event.slug}/")
        self.assertContains(page, "How projects are judged")
        self.assertContains(page, "33%")
        self.assertContains(page, "Submissions close")
        self.assertEqual(page.context["project_count"], 40)
        self.assertEqual(sum(t["n"] for t in page.context["tracks"]), 40)

    def test_timeline(self):
        t = timeline(self.event)
        self.assertEqual(t["marks"][0]["pct"], 0)
        self.assertEqual(t["marks"][-1]["pct"], 100)
        self.assertIn("judging opens", t["marks"][1]["label"])  # same moment as the deadline: one mark
        self.assertTrue(t["after"])
        self.assertIsNone(t["here"])
        middle = self.event.submissions_open_at + (self.event.submissions_close_at - self.event.submissions_open_at) / 2
        t = timeline(self.event, now=middle)
        self.assertTrue(0 < t["here"] < t["marks"][1]["pct"])
        self.assertEqual([m["past"] for m in t["marks"]], [True, False, False])

    def test_project_page_shows_place_overall_and_in_track(self):
        r = ProjectResult.objects.filter(event=self.event, project__track__isnull=False).order_by("rank")[5]
        page = self.client.get(r.project.get_absolute_url())
        self.assertContains(page, f"{r.adjusted_mean:.3f}")
        self.assertContains(page, f"adjusted / 5.00 · {r.review_count} judge")
        self.assertContains(page, f"<strong>{r.rank}</strong> of 40")
        peers = ProjectResult.objects.filter(event=self.event, project__track=r.project.track)
        self.assertContains(page, f"of {peers.count()}</td>")

    def test_project_page_before_publication_says_nothing(self):
        services.publish_results(self.event, self.organizer, False)
        project = Project.objects.get(external_id="prj_01")
        page = self.client.get(project.get_absolute_url())
        self.assertNotContains(page, 'id="result"')
        self.assertNotContains(page, "By criterion")


class SeedPublishTests(SeededTestCase):
    def test_publish_flag_makes_the_sample_a_finished_event(self):
        self.assertFalse(self.event.results_published)  # a plain seed does not publish
        Event.objects.all().delete()
        call_command("seed_fixtures", str(FIXTURES), "--publish", stdout=StringIO())
        event = Event.objects.get(external_id="evt_01")
        self.assertTrue(event.results_published)
        self.assertEqual(ProjectResult.objects.filter(event=event).count(), 40)
        self.assertEqual(self.client.get(f"/events/{event.slug}/results/").status_code, 200)
        self.assertContains(self.client.get("/"), "field-data")


class ScriptRuleTests(SimpleTestCase):
    def test_scripts_hold_no_colours_and_load_nothing(self):
        import re

        for path in (STATIC / "js").glob("*.js"):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(re.findall(r"#[0-9A-Fa-f]{6}\b|rgba?\(", text), [], path.name)
            self.assertEqual(re.findall(r"https?://|XMLHttpRequest|import\(|eval\(|innerHTML", text), [], path.name)
            # One script talks to the server, and only to the address the page gave it.
            if path.name != "review.js":
                self.assertNotIn("fetch(", text, path.name)
