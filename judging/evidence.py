"""Evidence for the normalization method: does it bring the order closer to
the truth than a plain average does?

On real data nobody knows the true order, so the question cannot be asked of
the fixture set. It can be asked of a simulated event, where each project is
given a true quality first and the judges' marks are made from it:

    mark = judge's leniency + judge's scale x (true quality - 3) + 3 + noise

rounded to the rubric's whole numbers and kept inside it. Every method then
sees the same marks, and its order is compared with the true one.

Nothing here is used by the portal at run time. `manage.py
normalization_evidence` prints the document in docs/.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .normalization import FLAT_EPSILON, Review, _mean, _stdev, normalize
from .pairwise import kendall_tau


@dataclass
class Panel:
    """What kind of event is simulated."""

    name: str
    says: str
    projects: int = 40
    judges: int = 30
    reviews: tuple[int, int] = (3, 3)  # fewest and most reviews a project gets
    leniency: float = 0.5  # spread of how generous judges are, in points
    scale: float = 0.25  # spread of how wide judges mark
    noise: float = 0.6  # disagreement that is nobody's habit, in points
    flat_judges: int = 0  # judges who give every project the same mark
    criteria: int = 3


PANELS = (
    Panel("Even panel", "Every project gets three reviews; judges differ a little.", leniency=0.3, scale=0.15),
    Panel("Harsh and generous", "As above, with judges a full point apart in habit.", leniency=1.0, scale=0.25),
    Panel(
        "Like the fixture set",
        "Two to five reviews per project, one judge who gives everyone the same mark.",
        reviews=(2, 5),
        leniency=0.6,
        flat_judges=1,
    ),
    Panel(
        "Few reviews, loud judges",
        "Two or three reviews per project and judges a point apart: the hardest case.",
        reviews=(2, 3),
        leniency=1.0,
        scale=0.35,
        noise=0.7,
    ),
    Panel("No habits at all", "Judges differ only by noise. There is nothing to correct.", leniency=0.0, scale=0.0),
)

METHODS = (
    ("raw", "Raw mean"),
    ("zscore", "Standard scores per judge"),
    ("zshrunk", "The same, shrunk toward the panel"),
    ("normalized", "Quality and leniency together"),
    ("adjusted", "The same, adjusted for jury size (what ranks)"),
)


def standard_scores(reviews: list[Review], shrink_k: float) -> dict[str, float]:
    """What this portal did before: each score read against its judge's own
    mean and spread, both pulled toward the panel's by n / (n + K). With K = 0
    it is the plain z-score most portals use. Kept here to be measured."""
    by_judge: dict[str, list[float]] = {}
    for r in reviews:
        by_judge.setdefault(r.judge_id, []).append(r.score)
    scores = [r.score for r in reviews]
    big_m = _mean(scores)
    big_s = _stdev(scores, big_m)
    if big_s < FLAT_EPSILON:
        big_s = 1.0
    judge = {}
    for j, xs in by_judge.items():
        m = _mean(xs)
        s = _stdev(xs, m)
        w = len(xs) / (len(xs) + shrink_k) if len(xs) + shrink_k else 1.0
        judge[j] = (w * m + (1 - w) * big_m, w * s + (1 - w) * big_s, len(xs) >= 2 and s < FLAT_EPSILON)
    parts: dict[str, list[float]] = {}
    for r in reviews:
        m, s, flat = judge[r.judge_id]
        parts.setdefault(r.project_id, []).append(0.0 if flat or s < FLAT_EPSILON else (r.score - m) / s)
    return {p: min(5.0, max(1.0, big_m + _mean(zs) * big_s)) for p, zs in parts.items()}


def simulate(panel: Panel, seed: int) -> tuple[dict[str, float], list[Review]]:
    """One event: the true quality of every project, and the marks given."""
    rng = random.Random(seed)
    truth = {f"p{n:02d}": min(5.0, max(1.0, rng.gauss(3.2, 0.8))) for n in range(panel.projects)}
    judges = [f"j{n:02d}" for n in range(panel.judges)]
    lean = {j: rng.gauss(0, panel.leniency) for j in judges}
    wide = {j: max(0.4, rng.gauss(1.0, panel.scale)) for j in judges}
    flat = {j: rng.choice((3, 4)) for j in judges[: panel.flat_judges]}
    load = dict.fromkeys(judges, 0)
    reviews = []
    for project, quality in truth.items():
        wanted = rng.randint(*panel.reviews)
        # The lightest-loaded judges, ties at random: what balanced assignment does.
        order = sorted(judges, key=lambda j: (load[j], rng.random()))
        for judge in order[:wanted]:
            load[judge] += 1
            marks = []
            for _ in range(panel.criteria):
                if judge in flat:
                    marks.append(flat[judge])
                    continue
                value = 3 + lean[judge] + wide[judge] * (quality - 3) + rng.gauss(0, panel.noise)
                marks.append(min(5, max(1, round(value))))
            reviews.append(Review(judge, project, sum(marks) / len(marks)))
    return truth, reviews


def orders(reviews: list[Review]) -> dict[str, dict[str, float]]:
    """The score every method gives every project."""
    ours = normalize(reviews, 1, 5)
    return {
        "raw": {k: p.raw_mean for k, p in ours.projects.items()},
        "zscore": standard_scores(reviews, 0),
        "zshrunk": standard_scores(reviews, 3),
        "normalized": {k: p.normalized for k, p in ours.projects.items()},
        "adjusted": {k: p.adjusted for k, p in ours.projects.items()},
    }


def constants(panel: Panel, values=(0.5, 1.0, 1.5, 2.0, 3.0, 6.0), runs: int = 300, seed: int = 2026) -> dict:
    """Agreement with the truth for other values of K, the judge's shrinkage."""
    total = dict.fromkeys(values, 0.0)
    for n in range(runs):
        truth, reviews = simulate(panel, seed * 1000 + n)
        for k in values:
            got = normalize(reviews, 1, 5, shrink_k=k)
            total[k] += kendall_tau({p: s.adjusted for p, s in got.projects.items()}, truth) or 0.0
    return {k: v / runs for k, v in total.items()}


def _places(scores: dict[str, float]) -> dict[str, int]:
    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    return {k: n for n, k in enumerate(ranked, start=1)}


def measure(truth: dict[str, float], scores: dict[str, float], top: int = 3) -> dict[str, float]:
    true_place, place = _places(truth), _places(scores)
    best = {k for k, n in true_place.items() if n <= top}
    found = {k for k, n in place.items() if n <= top}
    return {
        "tau": kendall_tau(scores, truth) or 0.0,
        "top": len(best & found) / top,
        "winner": 1.0 if place[min(true_place, key=true_place.get)] == 1 else 0.0,
        "off": sum(abs(place[k] - true_place[k]) for k in truth) / len(truth),
    }


def trial(panel: Panel, runs: int = 300, seed: int = 2026) -> dict[str, dict[str, float]]:
    """The measures of every method, averaged over many simulated events."""
    total = {m: {"tau": 0.0, "top": 0.0, "winner": 0.0, "off": 0.0, "beats_raw": 0.0} for m, _ in METHODS}
    for n in range(runs):
        truth, reviews = simulate(panel, seed * 1000 + n)
        got = {m: measure(truth, scores) for m, scores in orders(reviews).items()}
        for m, row in got.items():
            for key, value in row.items():
                total[m][key] += value
            total[m]["beats_raw"] += 1.0 if row["tau"] > got["raw"]["tau"] else 0.0
    return {m: {k: v / runs for k, v in row.items()} for m, row in total.items()}


def leave_one_out(reviews: list[Review], scale_min: float, scale_max: float) -> list[dict]:
    """Take each judge out in turn and rank again. How far does the order move?"""
    whole = normalize(reviews, scale_min, scale_max)
    before = {k: p.rank for k, p in whole.projects.items()}
    winners = {k for k, n in before.items() if n <= 3}
    out = []
    for judge in sorted({r.judge_id for r in reviews}):
        rest = [r for r in reviews if r.judge_id != judge]
        after = {k: p.rank for k, p in normalize(rest, scale_min, scale_max).projects.items()}
        moved = {k: abs(after[k] - before[k]) for k in after if k in before}
        out.append(
            {
                "judge": judge,
                "reviews": len(reviews) - len(rest),
                "largest": max(moved.values(), default=0),
                "mean": sum(moved.values()) / len(moved) if moved else 0.0,
                "top_kept": len(winners & {k for k, n in after.items() if n <= 3}),
                "dropped": len(before) - len(after),
            }
        )
    return out


def places_without_one_judge(reviews: list[Review], scale_min: float, scale_max: float) -> dict[str, dict]:
    """For every project, the best and the worst place it takes when any one
    judge is taken out. A project whose place holds whoever is missing has a
    place that can be trusted; one that swings rests on a single opinion."""
    whole = normalize(reviews, scale_min, scale_max)
    seen: dict[str, list[int]] = {k: [p.rank] for k, p in whole.projects.items()}
    for judge in sorted({r.judge_id for r in reviews}):
        rest = [r for r in reviews if r.judge_id != judge]
        for k, p in normalize(rest, scale_min, scale_max).projects.items():
            seen.setdefault(k, []).append(p.rank)
    return {k: {"best": min(v), "worst": max(v)} for k, v in seen.items()}
