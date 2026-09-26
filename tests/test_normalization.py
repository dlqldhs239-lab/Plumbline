"""The normalization maths, on synthetic cases and on the real fixtures."""

from django.test import SimpleTestCase

from judging import services
from judging.normalization import Review, normalize, weighted_score

from .base import SeededTestCase


class WeightedScoreTests(SimpleTestCase):
    def test_weights_apply(self):
        self.assertAlmostEqual(weighted_score({"a": 5, "b": 1}, {"a": 1, "b": 1}), 3.0)
        self.assertAlmostEqual(weighted_score({"a": 5, "b": 1}, {"a": 3, "b": 1}), 4.0)

    def test_missing_criteria_are_ignored(self):
        self.assertAlmostEqual(weighted_score({"a": 4}, {"a": 1, "b": 1}), 4.0)
        self.assertIsNone(weighted_score({}, {"a": 1}))


class NormalizeTests(SimpleTestCase):
    def test_harsh_and_generous_judges_are_reconciled(self):
        # Judge H scores everything one point lower than judge G on the same projects.
        reviews = []
        truth = {"p1": 2, "p2": 3, "p3": 4, "p4": 5}
        for pid, t in truth.items():
            reviews.append(Review("H", pid, t - 0.5))
            reviews.append(Review("G", pid, t + 0.5))
        # And a project that only the harsh judge saw.
        reviews.append(Review("H", "p5", 3.5))  # truly a 4 on G's scale
        res = normalize(reviews, 1, 5)
        ranks = {p: s.rank_normalized for p, s in res.projects.items()}
        self.assertEqual(ranks["p4"], 1)
        self.assertEqual(ranks["p1"], 5)
        # Raw mean would put p5 (3.5) below p3 (4.0). Normalized, p5 sits between p3 and p4-ish, above p2.
        self.assertLess(ranks["p5"], ranks["p2"])

    def test_flat_judge_is_rank_neutral(self):
        reviews = [Review("F", f"p{i}", 3.0) for i in range(4)]
        reviews += [Review("A", "p0", 5.0), Review("A", "p1", 4.0), Review("A", "p2", 2.0), Review("A", "p3", 1.0)]
        res = normalize(reviews, 1, 5)
        self.assertTrue(res.judges["F"].flat)
        order = sorted(res.projects.values(), key=lambda s: s.rank_normalized)
        self.assertEqual([s.project_id for s in order], ["p0", "p1", "p2", "p3"])
        for s in res.projects.values():
            self.assertTrue(all(z == 0.0 for z, r in zip(s.z_values, [r for r in reviews if r.project_id == s.project_id]) if r.judge_id == "F"))

    def test_single_review_judge_is_shrunk_toward_panel(self):
        reviews = [Review("A", "p1", 1.0), Review("A", "p2", 5.0), Review("A", "p3", 3.0), Review("B", "p3", 5.0)]
        res = normalize(reviews, 1, 5)
        self.assertLess(res.judges["B"].shrink_weight, 0.3)
        self.assertGreater(res.judges["A"].shrink_weight, 0.45)

    def test_ranks_are_dense_and_scores_clipped(self):
        reviews = [Review("A", "p1", 5.0), Review("A", "p2", 5.0), Review("A", "p3", 1.0)]
        res = normalize(reviews, 1, 5)
        ranks = sorted(s.rank_normalized for s in res.projects.values())
        self.assertEqual(ranks, [1, 1, 3])
        for s in res.projects.values():
            self.assertGreaterEqual(s.normalized, 1)
            self.assertLessEqual(s.normalized, 5)

    def test_empty_input(self):
        res = normalize([], 1, 5)
        self.assertEqual(res.projects, {})
        self.assertIsNone(res.panel_mean)


class FixtureNormalizationTests(SeededTestCase):
    def test_recompute_on_fixtures(self):
        from events.models import EventRole, Project
        from judging.models import JudgeAssignment, JudgeCalibration, ProjectResult

        dup = Project.objects.get(external_id="prj_41")
        dup_reviews = JudgeAssignment.objects.filter(project=dup).count()
        out = services.recompute_results(self.event, self.organizer)
        self.assertEqual(out["reviews"], 126 - dup_reviews)  # the duplicate is excluded from standings
        self.assertEqual(ProjectResult.objects.filter(event=self.event).count(), 40)
        flat = JudgeCalibration.objects.filter(event=self.event, flat=True)
        self.assertEqual(flat.count(), 1)
        flat_judge = flat.get().judge
        self.assertEqual(EventRole.objects.get(event=self.event, user=flat_judge, role="judge").external_id, "jdg_07")
        ranks = list(ProjectResult.objects.filter(event=self.event).values_list("rank_normalized", flat=True))
        self.assertIn(1, ranks)
        self.assertTrue(all(r is not None for r in ranks))
