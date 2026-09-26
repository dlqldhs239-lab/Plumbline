"""Cross-judge score normalization.

The problem: judges disagree about where "3" is. One marks everything a 3,
another never gives below 4, a third uses the whole scale. Averaging raw scores
then rewards projects that happened to draw generous judges.

The method here is per-judge standardization with shrinkage, mapped back to
the rubric scale. In plain terms:

1. For each completed review compute the judge's weighted score for the
   project on the rubric scale (weights from the rubric).
2. For each judge compute their mean and standard deviation over the projects
   they reviewed.
3. Judges who reviewed only a few projects have noisy statistics. Their mean
   and stdev are pulled toward the panel-wide values with weight
   n / (n + K) (K = SHRINK_K), a standard James–Stein style shrinkage.
4. Each review becomes a z-score: (score − judge_mean) / judge_stdev, using the
   shrunk statistics. A judge with zero spread cannot rank anything; their
   reviews get z = 0 (rank-neutral) and the judge is flagged as flat.
5. A project's normalized score is the mean z across its reviews, mapped back
   to the scale: panel_mean + z × panel_stdev, then clipped to [min, max].

Ranking uses the normalized score; the raw mean is kept beside it so an
organizer can see exactly what moved and why. See JUDGING.md for the
worked example on the fixture data.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

SHRINK_K = 3.0  # reviews needed before a judge's own statistics dominate
FLAT_EPSILON = 1e-9


@dataclass
class Review:
    judge_id: str
    project_id: str
    score: float  # weighted score on the rubric scale


@dataclass
class JudgeStats:
    judge_id: str
    n: int
    mean: float
    stdev: float
    shrunk_mean: float
    shrunk_stdev: float
    shrink_weight: float
    flat: bool


@dataclass
class ProjectStanding:
    project_id: str
    n: int
    raw_mean: float | None
    normalized: float | None
    rank_raw: int | None = None
    rank_normalized: int | None = None
    z_values: list[float] = field(default_factory=list)


@dataclass
class NormalizationResult:
    judges: dict[str, JudgeStats]
    projects: dict[str, ProjectStanding]
    panel_mean: float | None
    panel_stdev: float | None
    method: str = "zscore-shrink-v1"


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: list[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def weighted_score(values: dict[str, int], weights: dict[str, float]) -> float | None:
    """Weighted mean of criterion values on the rubric scale. Missing criteria
    are ignored (a partially filled review still counts for what it says)."""
    num = 0.0
    den = 0.0
    for key, weight in weights.items():
        if key in values and values[key] is not None:
            num += float(values[key]) * weight
            den += weight
    if den == 0:
        return None
    return num / den


def normalize(
    reviews: list[Review], scale_min: float, scale_max: float, shrink_k: float = SHRINK_K
) -> NormalizationResult:
    by_judge: dict[str, list[Review]] = defaultdict(list)
    by_project: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        by_judge[r.judge_id].append(r)
        by_project[r.project_id].append(r)

    all_scores = [r.score for r in reviews]
    panel_mean = _mean(all_scores) if all_scores else None
    panel_stdev = _stdev(all_scores, panel_mean) if panel_mean is not None else None
    if panel_stdev is not None and panel_stdev < FLAT_EPSILON:
        panel_stdev = 1.0  # everyone agrees on everything; z-scores are all zero anyway

    judges: dict[str, JudgeStats] = {}
    for judge_id, rs in by_judge.items():
        xs = [r.score for r in rs]
        n = len(xs)
        m = _mean(xs)
        s = _stdev(xs, m)
        w = n / (n + shrink_k)
        shrunk_mean = w * m + (1 - w) * panel_mean
        shrunk_stdev = w * s + (1 - w) * panel_stdev
        flat = n >= 2 and s < FLAT_EPSILON
        judges[judge_id] = JudgeStats(judge_id, n, m, s, shrunk_mean, shrunk_stdev, w, flat)

    projects: dict[str, ProjectStanding] = {}
    for project_id, rs in by_project.items():
        raw = _mean([r.score for r in rs])
        zs = []
        for r in rs:
            js = judges[r.judge_id]
            if js.flat or js.shrunk_stdev < FLAT_EPSILON:
                zs.append(0.0)
            else:
                zs.append((r.score - js.shrunk_mean) / js.shrunk_stdev)
        z = _mean(zs)
        normalized = panel_mean + z * panel_stdev
        normalized = max(scale_min, min(scale_max, normalized))
        projects[project_id] = ProjectStanding(project_id, len(rs), raw, normalized, z_values=zs)

    _rank(projects, key="raw_mean", attr="rank_raw")
    _rank(projects, key="normalized", attr="rank_normalized")
    return NormalizationResult(judges=judges, projects=projects, panel_mean=panel_mean, panel_stdev=panel_stdev)


def _rank(projects: dict[str, ProjectStanding], key: str, attr: str):
    """Dense ranking, highest first. Ties share a rank (1, 1, 3)."""
    ordered = sorted(projects.values(), key=lambda p: (-(getattr(p, key) or 0), p.project_id))
    rank = 0
    prev = None
    for i, p in enumerate(ordered, start=1):
        value = round(getattr(p, key) or 0, 9)
        if value != prev:
            rank = i
            prev = value
        setattr(p, attr, rank)
