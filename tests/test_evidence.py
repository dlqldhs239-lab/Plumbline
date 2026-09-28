"""The measurements behind docs/normalization-evidence.md."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from judging import evidence, services
from judging.normalization import Review, normalize

from .base import SeededTestCase


class SimulationTests(SimpleTestCase):
    def test_the_same_seed_gives_the_same_event(self):
        panel = evidence.PANELS[2]
        one, two = evidence.simulate(panel, 7), evidence.simulate(panel, 7)
        self.assertEqual(one[0], two[0])
        self.assertEqual(
            [(r.judge_id, r.project_id, r.score) for r in one[1]], [(r.judge_id, r.project_id, r.score) for r in two[1]]
        )
        self.assertNotEqual(one[0], evidence.simulate(panel, 8)[0])

    def test_an_event_is_what_the_panel_says(self):
        for panel in evidence.PANELS:
            truth, reviews = evidence.simulate(panel, 3)
            self.assertEqual(len(truth), panel.projects)
            per_project = {}
            for r in reviews:
                per_project[r.project_id] = per_project.get(r.project_id, 0) + 1
                self.assertTrue(1 <= r.score <= 5)
            self.assertTrue(all(panel.reviews[0] <= n <= panel.reviews[1] for n in per_project.values()), panel.name)
            self.assertEqual(len({(r.judge_id, r.project_id) for r in reviews}), len(reviews))
            flat = [j for j, s in normalize(reviews, 1, 5).judges.items() if s.flat]
            self.assertGreaterEqual(len(flat), panel.flat_judges if panel.flat_judges else 0)

    def test_a_perfect_order_measures_as_perfect(self):
        truth = {f"p{n}": float(n) for n in range(10)}
        got = evidence.measure(truth, dict(truth))
        self.assertEqual((got["tau"], got["top"], got["winner"], got["off"]), (1.0, 1.0, 1.0, 0.0))
        upside_down = {k: -v for k, v in truth.items()}
        worst = evidence.measure(truth, upside_down)
        self.assertEqual((worst["tau"], worst["winner"]), (-1.0, 0.0))
        self.assertEqual(worst["off"], 5.0)

    def test_where_judges_differ_the_method_beats_the_average(self):
        """The claim the document makes, held to on a small run."""
        loud = next(p for p in evidence.PANELS if p.name == "Harsh and generous")
        rows = evidence.trial(loud, runs=40)
        self.assertGreater(rows["adjusted"]["tau"], rows["raw"]["tau"] + 0.05)
        self.assertGreater(rows["adjusted"]["tau"], rows["zshrunk"]["tau"])
        self.assertGreater(rows["adjusted"]["beats_raw"], 0.9)

    def test_and_where_they_do_not_it_costs_something_and_the_table_says_so(self):
        quiet = next(p for p in evidence.PANELS if p.name == "No habits at all")
        rows = evidence.trial(quiet, runs=40)
        self.assertLess(rows["adjusted"]["tau"], rows["raw"]["tau"])
        self.assertGreater(rows["adjusted"]["tau"], rows["raw"]["tau"] - 0.06)

    def test_the_earlier_method_is_measured_as_it_was(self):
        reviews = [
            Review("A", "p1", 4.0),
            Review("A", "p2", 2.0),
            Review("B", "p1", 5.0),
            Review("B", "p2", 4.0),
            Review("B", "p3", 3.0),
        ]
        got = evidence.standard_scores(reviews, 3)
        # The figures JUDGING.md gave for this case while that method was in use.
        self.assertAlmostEqual(got["p1"], 4.531, places=2)
        self.assertAlmostEqual(got["p2"], 3.086, places=2)
        self.assertAlmostEqual(got["p3"], 2.748, places=2)


class FixtureEvidenceTests(SeededTestCase):
    def test_taking_a_judge_out(self):
        reviews, _, rubric = services.collect_reviews(self.event)
        rows = evidence.leave_one_out(reviews, rubric.scale_min, rubric.scale_max)
        self.assertEqual(len(rows), 30)
        self.assertEqual(sum(r["reviews"] for r in rows), len(reviews))
        self.assertTrue(all(0 <= r["top_kept"] <= 3 for r in rows))
        spans = evidence.places_without_one_judge(reviews, rubric.scale_min, rubric.scale_max)
        whole = normalize(reviews, rubric.scale_min, rubric.scale_max)
        for pid, s in whole.projects.items():
            self.assertLessEqual(spans[pid]["best"], s.rank)
            self.assertGreaterEqual(spans[pid]["worst"], s.rank)

    def test_the_document_prints(self):
        out = StringIO()
        call_command("normalization_evidence", self.event.slug, runs=2, stdout=out)
        text = out.getvalue()
        for heading in (
            "## 1. Does the method find the true order?",
            "### The constant K",
            "how much does the order depend on one judge?",
            "how far can a place be trusted?",
        ):
            self.assertIn(heading, text)
        for panel in evidence.PANELS:
            self.assertIn(f"### {panel.name}", text)
        self.assertNotIn("nan", text.lower().replace("finance", ""))
        with self.assertRaises(CommandError):
            call_command("normalization_evidence", "no-such-event", stdout=StringIO())
