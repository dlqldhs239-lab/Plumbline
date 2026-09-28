"""Cross-judge score normalization.

The problem: judges disagree about where "3" is. One never gives below 4,
another is sparing with anything above 3. Averaging raw scores then rewards
the projects that happened to draw generous judges.

The method here estimates two things at once, each given the other: how
good every project is, and how lenient every judge is.

    score(judge, project) = M + quality(project) + leniency(judge) + noise

1. For each submitted review compute the judge's weighted score for the
   project on the rubric scale (weights from the rubric).
2. A judge who gave every project the same score has expressed no preference
   between them. Their reviews are set aside: they are counted and shown, and
   they say nothing about any project. M is the mean of the reviews that
   are left.
3. A project's quality is the mean of what its reviews say once each judge's
   leniency is taken off. A judge's leniency is the mean of what is left of
   their scores once each project's quality is taken off, pulled toward zero
   with weight n / (n + K): a judge with two reviews is believed less than
   one with ten. K = SHRINK_K. The two are solved together by repeating one
   after the other until nothing moves.
4. A project's normalized score is M + quality, kept inside the scale.
5. Projects do not all get the same number of reviews. A mean of two reviews
   is a weaker claim than a mean of five, so each project is pulled toward
   the panel mean in proportion to how little evidence it has:
   adjusted = (n × normalized + J × M) / (n + J), with J = jury_k and n the
   number of its reviews that say something. J = 0 switches the step off.

Why not standard scores per judge, which is what the first two versions of
this module did? Because a judge's mean depends on which projects they drew.
With three or four reviews a judge, someone who happened to get strong
projects looks generous and is corrected for it. Estimating both sides
together does not make that mistake. docs/normalization-evidence.md has the
measurements, including the cases where no correction at all does better.

Ranking uses the adjusted score. The raw mean and the normalized score are
kept beside it, each with its own rank, so an organizer can see exactly what
moved at which step and why. See JUDGING.md for the worked example.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

SHRINK_K = 1.0  # reviews at no leniency that every judge is given to start with
JURY_K = 3.0  # reviews the panel mean counts for when a project has few
FLAT_EPSILON = 1e-9
SETTLED = 1e-12  # the solving stops when no estimate moves by more than this
MAX_ROUNDS = 5000
METHOD = "additive-shrink-v3"


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
    shrunk_mean: float  # the panel mean plus this judge's leniency
    shrunk_stdev: float  # kept for the stored table; this method does not rescale
    shrink_weight: float
    flat: bool
    leniency: float = 0.0


@dataclass
class ProjectStanding:
    project_id: str
    n: int
    raw_mean: float | None
    normalized: float | None
    adjusted: float | None = None
    jury_weight: float | None = None
    rank_raw: int | None = None
    rank_normalized: int | None = None
    rank: int | None = None
    # What each review says of the project with its judge's leniency taken
    # off, as a distance from the panel mean. Zero for a review set aside.
    z_values: list[float] = field(default_factory=list)
    informative: int = 0


@dataclass
class NormalizationResult:
    judges: dict[str, JudgeStats]
    projects: dict[str, ProjectStanding]
    panel_mean: float | None
    panel_stdev: float | None
    shrink_k: float = SHRINK_K
    jury_k: float = JURY_K
    method: str = METHOD
    rounds: int = 0
    settled: bool = True


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
    reviews: list[Review],
    scale_min: float,
    scale_max: float,
    shrink_k: float = SHRINK_K,
    jury_k: float = JURY_K,
) -> NormalizationResult:
    if shrink_k < 0 or jury_k < 0:
        raise ValueError("shrinkage constants cannot be negative")
    by_judge: dict[str, list[Review]] = defaultdict(list)
    by_project: dict[str, list[Review]] = defaultdict(list)
    for r in reviews:
        by_judge[r.judge_id].append(r)
        by_project[r.project_id].append(r)

    raw = {}
    for judge_id, rs in by_judge.items():
        xs = [r.score for r in rs]
        m = _mean(xs)
        s = _stdev(xs, m)
        raw[judge_id] = (len(xs), m, s, len(xs) >= 2 and s < FLAT_EPSILON)
    flat = {j for j, (_, _, _, is_flat) in raw.items() if is_flat}

    # The panel mean is taken over the reviews that say something. A judge
    # who gave everyone a two must not lower the level the others are read
    # against. Only if nobody said anything is it the mean of what there is.
    all_scores = [r.score for r in reviews if r.judge_id not in flat] or [r.score for r in reviews]
    panel_mean = _mean(all_scores) if all_scores else None
    panel_stdev = _stdev(all_scores, panel_mean) if panel_mean is not None else None
    if panel_stdev is not None and panel_stdev < FLAT_EPSILON:
        panel_stdev = 1.0  # everyone agrees on everything

    # Solved in a fixed order, so that the same reviews give the same figures
    # to the last digit whatever order they arrive in.
    judge_ids = sorted(j for j in by_judge if j not in flat)
    project_ids = sorted(by_project)
    said = {p: [r for r in by_project[p] if r.judge_id not in flat] for p in project_ids}
    leniency = dict.fromkeys(by_judge, 0.0)
    quality = dict.fromkeys(project_ids, 0.0)
    rounds, settled = 0, True
    if reviews:
        settled = False
        for rounds in range(1, MAX_ROUNDS + 1):  # noqa: B007 - the count is reported
            moved = 0.0
            for p in project_ids:
                rs = said[p]
                new = sum(r.score - panel_mean - leniency[r.judge_id] for r in rs) / len(rs) if rs else 0.0
                moved = max(moved, abs(new - quality[p]))
                quality[p] = new
            for j in judge_ids:
                rs = by_judge[j]
                if len(rs) + shrink_k <= 0:
                    continue
                new = sum(r.score - panel_mean - quality[r.project_id] for r in rs) / (len(rs) + shrink_k)
                moved = max(moved, abs(new - leniency[j]))
                leniency[j] = new
            if moved < SETTLED:
                settled = True
                break

    judges: dict[str, JudgeStats] = {}
    for judge_id, (n, m, s, is_flat) in raw.items():
        w = n / (n + shrink_k) if n + shrink_k > 0 else 1.0
        if is_flat:
            # Shown beside the others; used for nothing.
            leniency[judge_id] = w * (m - panel_mean)
        judges[judge_id] = JudgeStats(
            judge_id, n, m, s, panel_mean + leniency[judge_id], s, w, is_flat, leniency=leniency[judge_id]
        )

    projects: dict[str, ProjectStanding] = {}
    for project_id in project_ids:
        rs = by_project[project_id]
        raw_mean = _mean([r.score for r in rs])
        parts = [0.0 if r.judge_id in flat else r.score - panel_mean - leniency[r.judge_id] for r in rs]
        normalized = max(scale_min, min(scale_max, panel_mean + quality[project_id]))
        n = len(said[project_id])
        jury_weight = n / (n + jury_k) if n + jury_k > 0 else 0.0
        adjusted = jury_weight * normalized + (1 - jury_weight) * panel_mean
        projects[project_id] = ProjectStanding(
            project_id,
            len(rs),
            raw_mean,
            normalized,
            adjusted=adjusted,
            jury_weight=jury_weight,
            z_values=parts,
            informative=n,
        )

    _rank(projects, key="raw_mean", attr="rank_raw")
    _rank(projects, key="normalized", attr="rank_normalized")
    _rank(projects, key="adjusted", attr="rank")
    return NormalizationResult(
        judges=judges,
        projects=projects,
        panel_mean=panel_mean,
        panel_stdev=panel_stdev,
        shrink_k=shrink_k,
        jury_k=jury_k,
        rounds=rounds,
        settled=settled,
    )


def _rank(projects: dict[str, ProjectStanding], key: str, attr: str):
    """Standard competition ranking, highest first: ties share a rank and the
    next rank is skipped (1, 1, 3)."""
    ordered = sorted(projects.values(), key=lambda p: (-(getattr(p, key) or 0), p.project_id))
    rank = 0
    prev = None
    for i, p in enumerate(ordered, start=1):
        value = round(getattr(p, key) or 0, 9)
        if value != prev:
            rank = i
            prev = value
        setattr(p, attr, rank)
