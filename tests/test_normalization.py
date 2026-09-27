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
            project_reviews = [r for r in reviews if r.project_id == s.project_id]
            self.assertTrue(
                all(z == 0.0 for z, r in zip(s.z_values, project_reviews, strict=True) if r.judge_id == "F")
            )

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


class JurySizeTests(SimpleTestCase):
    """Step 6: a mean of two reviews is a weaker claim than a mean of five."""

    def reviews(self):
        # One judge per review so judge normalization has nothing to say and
        # the jury-size step is seen on its own.
        out = [Review(f"a{i}", "few", 5.0) for i in range(2)]
        out += [Review(f"b{i}", "many", 4.6) for i in range(8)]
        out += [Review(f"c{i}", "low", 2.0) for i in range(5)]
        return out

    def test_a_well_reviewed_project_outranks_a_thinly_reviewed_one(self):
        off = normalize(self.reviews(), 1, 5, shrink_k=1e9, jury_k=0)
        self.assertEqual(off.projects["few"].rank, 1)
        self.assertAlmostEqual(off.projects["few"].adjusted, off.projects["few"].normalized)
        on = normalize(self.reviews(), 1, 5, shrink_k=1e9, jury_k=3)
        self.assertEqual(on.projects["many"].rank, 1)
        self.assertEqual(on.projects["few"].rank, 2)
        self.assertEqual(on.projects["few"].rank_normalized, 1)

    def test_the_formula(self):
        res = normalize(self.reviews(), 1, 5, shrink_k=1e9, jury_k=3)
        few = res.projects["few"]
        self.assertAlmostEqual(few.jury_weight, 2 / 5)
        self.assertAlmostEqual(few.adjusted, (2 * few.normalized + 3 * res.panel_mean) / 5)

    def test_the_adjustment_never_moves_a_score_past_the_panel_mean(self):
        res = normalize(self.reviews(), 1, 5, jury_k=10)
        for s in res.projects.values():
            lo, hi = sorted((s.normalized, res.panel_mean))
            self.assertGreaterEqual(s.adjusted, lo - 1e-12)
            self.assertLessEqual(s.adjusted, hi + 1e-12)

    def test_equal_review_counts_keep_their_order(self):
        reviews = []
        for j in range(4):
            for p, score in (("p1", 4.5), ("p2", 3.0), ("p3", 2.0 + j * 0.1)):
                reviews.append(Review(f"j{j}", p, score))
        for k in (0, 3, 10, 50):
            res = normalize(reviews, 1, 5, jury_k=k)
            for s in res.projects.values():
                self.assertEqual(s.rank, s.rank_normalized, k)

    def test_negative_constants_are_refused(self):
        with self.assertRaises(ValueError):
            normalize(self.reviews(), 1, 5, jury_k=-1)


def oracle(reviews, scale_min, scale_max, judge_k, jury_k):
    """The same method written a second time, from JUDGING.md and not from
    the module, with the standard library's statistics functions. If the two
    disagree, one of them does not do what the document says."""
    import statistics

    scores = [r.score for r in reviews]
    big_m = statistics.fmean(scores)
    big_s = statistics.stdev(scores) if len(scores) > 1 else 0.0
    if big_s < 1e-9:
        big_s = 1.0
    judge = {}
    for j in {r.judge_id for r in reviews}:
        xs = [r.score for r in reviews if r.judge_id == j]
        m = statistics.fmean(xs)
        s = statistics.stdev(xs) if len(xs) > 1 else 0.0
        w = len(xs) / (len(xs) + judge_k)
        judge[j] = (w * m + (1 - w) * big_m, w * s + (1 - w) * big_s, len(xs) >= 2 and s < 1e-9)
    out = {}
    for p in {r.project_id for r in reviews}:
        zs = []
        for r in reviews:
            if r.project_id != p:
                continue
            m, s, flat = judge[r.judge_id]
            zs.append(0.0 if flat or s < 1e-9 else (r.score - m) / s)
        normalized = min(scale_max, max(scale_min, big_m + statistics.fmean(zs) * big_s))
        n = len(zs)
        out[p] = (normalized, (n * normalized + jury_k * big_m) / (n + jury_k))
    return out


class OracleTests(SeededTestCase):
    def test_the_module_agrees_with_an_independent_implementation_on_the_fixtures(self):
        reviews, _, rubric = services.collect_reviews(self.event)
        self.assertEqual(len(reviews), 122)
        for judge_k, jury_k in ((3, 3), (3, 0), (3, 10), (0, 3), (10, 1)):
            res = normalize(reviews, rubric.scale_min, rubric.scale_max, shrink_k=judge_k, jury_k=jury_k)
            want = oracle(reviews, rubric.scale_min, rubric.scale_max, judge_k, jury_k)
            self.assertEqual(set(res.projects), set(want))
            for pid, (normalized, adjusted) in want.items():
                self.assertAlmostEqual(res.projects[pid].normalized, normalized, places=9)
                self.assertAlmostEqual(res.projects[pid].adjusted, adjusted, places=9)

    def test_a_hand_worked_case(self):
        """Small enough to check with a pencil; the arithmetic is in JUDGING.md."""
        reviews = [
            Review("A", "p1", 4.0),
            Review("A", "p2", 2.0),
            Review("B", "p1", 5.0),
            Review("B", "p2", 4.0),
            Review("B", "p3", 3.0),
        ]
        res = normalize(reviews, 1, 5, shrink_k=3, jury_k=3)
        self.assertAlmostEqual(res.panel_mean, 3.6)
        self.assertAlmostEqual(res.panel_stdev, 1.140175425, places=8)
        self.assertAlmostEqual(res.judges["A"].shrunk_mean, 0.4 * 3.0 + 0.6 * 3.6)
        self.assertAlmostEqual(res.judges["B"].shrunk_mean, 0.5 * 4.0 + 0.5 * 3.6)
        want = oracle(reviews, 1, 5, 3, 3)
        for pid in ("p1", "p2", "p3"):
            self.assertAlmostEqual(res.projects[pid].adjusted, want[pid][1], places=9)
        self.assertEqual(res.projects["p1"].rank, 1)


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
        rows = list(ProjectResult.objects.filter(event=self.event))
        self.assertEqual([r.rank for r in rows], sorted(r.rank for r in rows))
        self.assertTrue(all(r.adjusted_mean is not None for r in rows))
        first = rows[0]
        self.assertEqual([c["key"] for c in first.criterion_means], ["functionality", "quality", "innovation"])
        live = {c["key"]: c["mean"] for c in services.criterion_means(first.project)}
        for c in first.criterion_means:
            self.assertAlmostEqual(c["mean"], live[c["key"]], places=3)
            self.assertEqual(c["n"], first.review_count)

    def test_jury_k_follows_the_event_unless_set(self):
        from django.core.exceptions import PermissionDenied, ValidationError

        from judging.models import ProjectResult

        rubric = services.ensure_rubric(self.event)
        self.assertEqual(rubric.effective_jury_k(), 3.0)
        services.set_jury_k(rubric, self.organizer, "10")
        self.assertEqual(services.recompute_results(self.event, self.organizer)["jury_k"], 10.0)
        services.set_jury_k(rubric, self.organizer, 0)
        services.recompute_results(self.event, self.organizer)
        for r in ProjectResult.objects.filter(event=self.event):
            self.assertAlmostEqual(r.adjusted_mean, r.normalized_mean)
            self.assertEqual(r.rank, r.rank_normalized)
        with self.assertRaises(ValidationError):
            services.set_jury_k(rubric, self.organizer, 500)
        with self.assertRaises(PermissionDenied):
            services.set_jury_k(rubric, self.judge_a, 3)
        self.assertEqual(self.event.audit_entries.filter(action="rubric.jury_k").count(), 2)
