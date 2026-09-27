"""The Bradley-Terry estimator: cases with a known answer, a second
implementation, and the properties a ranking has to have."""

import math
import random

from django.test import SimpleTestCase

from judging.pairwise import Comparison, estimate, from_scores, kendall_tau, win_probability


def wins(a, b, n):
    return [Comparison(a, b) for _ in range(n)]


def oracle(comparisons, ids, prior, steps=200_000, rate=0.01):
    """The same maximum, found another way: plain gradient ascent on the log
    likelihood in log strengths, with the phantom opponent written into the
    objective. Slow and simple. If this and the MM algorithm disagree, one
    of them is wrong."""
    theta = {i: 0.0 for i in ids}
    for _ in range(steps):
        grad = {i: 0.0 for i in ids}
        for c in comparisons:
            a, b = c.winner, c.loser
            pa = 1 / (1 + math.exp(theta[b] - theta[a]))
            if c.tie:
                grad[a] += 0.5 - pa
                grad[b] += pa - 0.5
            else:
                grad[a] += 1 - pa
                grad[b] -= 1 - pa
        for i in ids:
            q = 1 / (1 + math.exp(-theta[i]))  # chance of beating the phantom
            grad[i] += prior * (1 - q) - prior * q
        step = max(abs(g) for g in grad.values())
        for i in ids:
            theta[i] += rate * grad[i]
        if step < 1e-12:
            break
    mean = sum(theta.values()) / len(theta)
    return {i: theta[i] - mean for i in ids}


class KnownAnswerTests(SimpleTestCase):
    def test_two_projects_three_wins_to_one(self):
        """With no prior the maximum is where the odds match the record: 3 to 1."""
        res = estimate(wins("a", "b", 3) + wins("b", "a", 1), prior=0)
        self.assertTrue(res.converged)
        a, b = res.projects["a"], res.projects["b"]
        self.assertAlmostEqual(a.strength / b.strength, 3.0, places=6)
        self.assertAlmostEqual(win_probability(a.strength, b.strength), 0.75, places=7)
        self.assertAlmostEqual(a.strength * b.strength, 1.0, places=9)  # geometric mean 1
        self.assertEqual((a.rank, b.rank), (1, 2))

    def test_a_cycle_of_equal_strength(self):
        """a beats b, b beats c, c beats a, once each: nobody is better."""
        res = estimate([Comparison("a", "b"), Comparison("b", "c"), Comparison("c", "a")], prior=0)
        for s in res.projects.values():
            self.assertAlmostEqual(s.strength, 1.0, places=7)
        self.assertEqual({s.rank for s in res.projects.values()}, {1})

    def test_three_projects_satisfy_the_likelihood_equations(self):
        """A beat B 2 to 1, A beat C 3 to 1, B and C split 2 to 2. At the
        maximum every strength equals its wins over the sum of n / (p_i + p_j);
        check that directly, and the order. B and C won three each, but C
        lost one more to A, so B stands above C."""
        games = wins("A", "B", 2) + wins("B", "A", 1) + wins("A", "C", 3) + wins("C", "A", 1)
        games += wins("B", "C", 2) + wins("C", "B", 2)
        res = estimate(games, prior=0)
        p = {k: v.strength for k, v in res.projects.items()}
        n = {("A", "B"): 3, ("A", "C"): 4, ("B", "C"): 4}
        won = {"A": 5, "B": 3, "C": 3}
        for i in "ABC":
            total = sum(c / (p[i] + p[j if i == k else k]) for (k, j), c in n.items() if i in (k, j))
            self.assertAlmostEqual(p[i], won[i] / total, places=7)
        self.assertEqual([res.projects[i].rank for i in "ABC"], [1, 2, 3])

    def test_ties_are_half_a_win_each(self):
        res = estimate([Comparison("a", "b", tie=True)] * 4, prior=0)
        self.assertAlmostEqual(res.projects["a"].strength, res.projects["b"].strength, places=9)
        same = estimate(wins("a", "b", 2) + wins("b", "a", 2), prior=0)
        self.assertAlmostEqual(res.projects["a"].strength, same.projects["a"].strength, places=9)


class OracleTests(SimpleTestCase):
    def test_agrees_with_gradient_ascent(self):
        rng = random.Random(7)
        ids = [f"p{i}" for i in range(9)]
        truth = {i: rng.uniform(-1.5, 1.5) for i in ids}
        games = []
        for _ in range(260):
            a, b = rng.sample(ids, 2)
            pa = 1 / (1 + math.exp(truth[b] - truth[a]))
            games.append(Comparison(a, b) if rng.random() < pa else Comparison(b, a))
        for prior in (0.5, 2.0):
            res = estimate(games, ids, prior=prior)
            self.assertTrue(res.converged)
            want = oracle(games, ids, prior)
            for i in ids:
                self.assertAlmostEqual(res.projects[i].score, want[i], places=5)

    def test_it_recovers_the_order_it_was_given(self):
        rng = random.Random(11)
        ids = [f"p{i}" for i in range(12)]
        truth = {i: (11 - n) * 0.5 for n, i in enumerate(ids)}
        games = []
        for _ in range(3000):
            a, b = rng.sample(ids, 2)
            pa = 1 / (1 + math.exp(truth[b] - truth[a]))
            games.append(Comparison(a, b) if rng.random() < pa else Comparison(b, a))
        res = estimate(games, ids)
        tau = kendall_tau({i: s.score for i, s in res.projects.items()}, truth)
        self.assertGreater(tau, 0.9)


class AwkwardInputTests(SimpleTestCase):
    def test_an_unbeaten_project_stays_finite(self):
        res = estimate(wins("a", "b", 5) + wins("b", "c", 5))
        self.assertTrue(res.converged)
        for s in res.projects.values():
            self.assertTrue(math.isfinite(s.score))
        self.assertEqual([res.projects[i].rank for i in "abc"], [1, 2, 3])

    def test_without_the_prior_it_says_it_did_not_settle(self):
        res = estimate(wins("a", "b", 5), prior=0, max_iterations=200)
        self.assertFalse(res.converged)  # a's strength grows without bound
        self.assertEqual(res.projects["a"].rank, 1)

    def test_groups_never_compared_across_are_reported(self):
        res = estimate(wins("a", "b", 3) + wins("b", "a", 1) + wins("c", "d", 2) + wins("d", "c", 2))
        self.assertEqual(res.components, 2)
        self.assertTrue(res.converged)
        self.assertEqual(estimate(wins("a", "b", 1) + wins("b", "c", 1)).components, 1)

    def test_a_project_nobody_compared_is_average_and_unranked(self):
        res = estimate(wins("a", "b", 3), projects=["a", "b", "z"])
        self.assertEqual(res.unheard, ["z"])
        self.assertIsNone(res.projects["z"].rank)
        self.assertAlmostEqual(res.projects["z"].strength * res.projects["a"].strength * res.projects["b"].strength, 1)
        self.assertLess(res.projects["b"].score, res.projects["z"].score)
        self.assertLess(res.projects["z"].score, res.projects["a"].score)

    def test_nothing_at_all(self):
        res = estimate([])
        self.assertEqual((res.projects, res.comparisons, res.converged), ({}, 0, True))
        res = estimate([], projects=["a"])
        self.assertIsNone(res.projects["a"].rank)

    def test_refusals(self):
        with self.assertRaises(ValueError):
            estimate([Comparison("a", "a")])
        with self.assertRaises(ValueError):
            estimate(wins("a", "b", 1), prior=-1)

    def test_more_evidence_moves_further_from_average(self):
        few = estimate(wins("a", "b", 2)).projects["a"].score
        many = estimate(wins("a", "b", 20)).projects["a"].score
        self.assertGreater(many, few)

    def test_order_of_input_does_not_matter(self):
        games = wins("a", "b", 3) + wins("b", "c", 2) + wins("c", "a", 1) + wins("a", "c", 4)
        shuffled = games[:]
        random.Random(3).shuffle(shuffled)
        one, two = estimate(games), estimate(shuffled)
        for i in "abc":
            self.assertAlmostEqual(one.projects[i].score, two.projects[i].score, places=9)


class FromScoresTests(SimpleTestCase):
    def test_a_harsh_and_a_generous_judge_agree_on_every_comparison(self):
        harsh = [("H", "p1", 1.0), ("H", "p2", 2.0), ("H", "p3", 3.0)]
        generous = [("G", "p1", 3.0), ("G", "p2", 4.0), ("G", "p3", 5.0)]
        games = from_scores(harsh + generous)
        self.assertEqual(len(games), 6)
        self.assertTrue(all(g.winner > g.loser for g in games))  # p3 over p2 over p1, from both
        res = estimate(games)
        self.assertEqual([res.projects[p].rank for p in ("p3", "p2", "p1")], [1, 2, 3])

    def test_equal_scores_are_ties_and_a_flat_judge_says_nothing(self):
        games = from_scores([("F", "p1", 4.0), ("F", "p2", 4.0), ("F", "p3", 4.0)])
        self.assertTrue(all(g.tie for g in games))
        res = estimate(games)
        for s in res.projects.values():
            self.assertAlmostEqual(s.score, 0.0, places=9)

    def test_no_comparison_crosses_judges(self):
        games = from_scores([("A", "p1", 5.0), ("B", "p2", 1.0)])
        self.assertEqual(games, [])


class KendallTests(SimpleTestCase):
    def test_bounds(self):
        a = {"x": 3, "y": 2, "z": 1}
        self.assertAlmostEqual(kendall_tau(a, a), 1.0)
        self.assertAlmostEqual(kendall_tau(a, {"x": 1, "y": 2, "z": 3}), -1.0)
        self.assertIsNone(kendall_tau({"x": 1}, {"x": 1}))
        self.assertIsNone(kendall_tau(a, {"x": 1, "y": 1, "z": 1}))
