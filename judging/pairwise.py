"""Pairwise judging: a Bradley-Terry estimator.

A rubric asks a judge "how good is this, from 1 to 5?", and then has to undo
the fact that judges disagree about where 3 is. A comparison asks "which of
these two is better?". A harsh judge and a generous judge can disagree on
every score and still agree on every comparison, so there is nothing to
undo.

The model. Each project i has a strength p_i > 0, and the chance that i is
preferred to j is

    P(i beats j) = p_i / (p_i + p_j)

Given the comparisons that were made, the strengths that make them most
likely are found with the MM algorithm (Hunter, 2004): starting from equal
strengths, repeat

    p_i  <-  W_i / sum over j of  n_ij / (p_i + p_j)

where W_i is the number of comparisons i won and n_ij the number made
between i and j, then rescale so the strengths have geometric mean 1. Each
step increases the likelihood, and it converges to the maximum whenever one
exists.

One does not always exist. A project that won every comparison has infinite
strength; two groups of projects never compared with each other have no
common scale. So every project also plays a phantom opponent of strength 1,
winning `prior` times and losing `prior` times (default half a comparison
each way). That is a weak Bayesian prior centred on "average": it ties the
groups together, keeps every strength finite, and fades as real comparisons
arrive. With prior = 0 the estimator is the plain maximum likelihood one.

A tie counts as half a win for each side.

Standard library only.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

PRIOR = 0.5
TOLERANCE = 1e-10
MAX_ITERATIONS = 10_000


@dataclass(frozen=True)
class Comparison:
    """`winner` and `loser` are project ids. `tie=True` means neither was preferred."""

    winner: str
    loser: str
    judge: str = ""
    tie: bool = False


@dataclass
class Standing:
    project_id: str
    strength: float  # p_i, geometric mean 1 over the event
    score: float  # log strength: 0 is average, differences are log odds
    wins: float
    losses: float
    comparisons: int
    rank: int | None = None


@dataclass
class PairwiseResult:
    projects: dict[str, Standing]
    iterations: int
    converged: bool
    comparisons: int
    prior: float
    log_likelihood: float
    method: str = "bradley-terry-mm-v1"
    components: int = 1
    unheard: list[str] = field(default_factory=list)


def win_probability(p_i: float, p_j: float) -> float:
    return p_i / (p_i + p_j)


def estimate(
    comparisons: list[Comparison],
    projects: list[str] | None = None,
    prior: float = PRIOR,
    tolerance: float = TOLERANCE,
    max_iterations: int = MAX_ITERATIONS,
) -> PairwiseResult:
    """Strengths for every project named in `projects` or in a comparison."""
    if prior < 0:
        raise ValueError("the prior cannot be negative")
    ids = list(dict.fromkeys(projects or []))
    seen = set(ids)
    for c in comparisons:
        for pid in (c.winner, c.loser):
            if pid not in seen:
                seen.add(pid)
                ids.append(pid)
        if c.winner == c.loser:
            raise ValueError("a project cannot be compared with itself")

    wins: dict[str, float] = defaultdict(float)
    losses: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    pairs: dict[tuple[str, str], float] = defaultdict(float)
    for c in comparisons:
        a, b = c.winner, c.loser
        if c.tie:
            wins[a] += 0.5
            wins[b] += 0.5
            losses[a] += 0.5
            losses[b] += 0.5
        else:
            wins[a] += 1
            losses[b] += 1
        count[a] += 1
        count[b] += 1
        pairs[(a, b) if a < b else (b, a)] += 1

    opponents: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (a, b), n in pairs.items():
        opponents[a].append((b, n))
        opponents[b].append((a, n))

    p = {i: 1.0 for i in ids}
    iterations, converged = 0, not ids
    # Without the prior a maximum exists only if every compared project has
    # both won and lost. Otherwise a strength runs off to infinity or to
    # zero; the order is still reported, and so is the fact that the numbers
    # did not settle.
    exists = prior > 0 or all(wins[i] > 0 and losses[i] > 0 for i in ids if count[i])
    for iterations in range(1, max_iterations + 1):  # noqa: B007 - the count is reported
        new = {}
        for i in ids:
            w = wins[i] + prior
            denominator = (2 * prior) / (p[i] + 1.0) if prior else 0.0
            for j, n in opponents[i]:
                denominator += n / (p[i] + p[j])
            if denominator <= 0:
                new[i] = p[i]  # never compared and no prior: nothing is known
            else:
                new[i] = max(w / denominator, 1e-300)
        if prior == 0:
            # Fix the scale: geometric mean 1. With the prior the phantom
            # opponent of strength 1 fixes it already.
            logs = [math.log(v) for v in new.values()]
            shift = sum(logs) / len(logs) if logs else 0.0
            new = {i: math.exp(math.log(v) - shift) for i, v in new.items()}
        change = max((abs(math.log(new[i]) - math.log(p[i])) for i in ids), default=0.0)
        p = new
        if change < tolerance:
            converged = exists
            break

    logs = [math.log(v) for v in p.values()]
    shift = sum(logs) / len(logs) if logs else 0.0
    standings = {}
    for i in ids:
        score = math.log(p[i]) - shift
        standings[i] = Standing(
            project_id=i,
            strength=math.exp(score),
            score=score,
            wins=wins[i],
            losses=losses[i],
            comparisons=count[i],
        )
    _rank(standings)

    likelihood = 0.0
    for c in comparisons:
        a, b = p[c.winner], p[c.loser]
        if c.tie:
            likelihood += 0.5 * (math.log(a / (a + b)) + math.log(b / (a + b)))
        else:
            likelihood += math.log(a / (a + b))

    return PairwiseResult(
        projects=standings,
        iterations=iterations,
        converged=converged,
        comparisons=len(comparisons),
        prior=prior,
        log_likelihood=likelihood,
        components=_components(ids, pairs),
        unheard=[i for i in ids if count[i] == 0],
    )


def _rank(standings: dict[str, Standing]):
    """Standard competition ranking on the score, highest first. Projects with
    no comparison at all are not ranked: nothing was said about them."""
    heard = sorted((s for s in standings.values() if s.comparisons), key=lambda s: (-s.score, s.project_id))
    rank, previous = 0, None
    for position, s in enumerate(heard, start=1):
        value = round(s.score, 9)
        if value != previous:
            rank, previous = position, value
        s.rank = rank


def _components(ids: list[str], pairs: dict[tuple[str, str], float]) -> int:
    """How many groups of projects were never compared across. More than one
    means the order between groups rests on the prior alone."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    return len({find(i) for i in ids if any(i in pair for pair in pairs)}) or (1 if ids else 0)


def from_scores(reviews: list[tuple[str, str, float]], margin: float = 1e-9) -> list[Comparison]:
    """Comparisons implied by rubric scores: within one judge, of every two
    projects they scored, the one with the higher score is preferred.

    `reviews` is (judge, project, score). Comparisons are only ever made
    inside one judge's own reviews, which is why this needs no normalization:
    a judge's harshness cancels out of their own comparison."""
    by_judge: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for judge, project, score in reviews:
        by_judge[judge].append((project, score))
    out = []
    for judge in sorted(by_judge):
        scored = sorted(by_judge[judge])
        for x in range(len(scored)):
            for y in range(x + 1, len(scored)):
                (a, sa), (b, sb) = scored[x], scored[y]
                if abs(sa - sb) <= margin:
                    out.append(Comparison(a, b, judge, tie=True))
                elif sa > sb:
                    out.append(Comparison(a, b, judge))
                else:
                    out.append(Comparison(b, a, judge))
    return out


def kendall_tau(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Agreement between two orderings of the same projects, from -1 to 1
    (tau-b, which allows ties). `a` and `b` map project to score, higher
    better. None if there is nothing to compare."""
    keys = sorted(set(a) & set(b))
    concordant = discordant = ties_a = ties_b = 0
    for x in range(len(keys)):
        for y in range(x + 1, len(keys)):
            da = a[keys[x]] - a[keys[y]]
            db = b[keys[x]] - b[keys[y]]
            if abs(da) < 1e-12 and abs(db) < 1e-12:
                continue
            if abs(da) < 1e-12:
                ties_a += 1
            elif abs(db) < 1e-12:
                ties_b += 1
            elif (da > 0) == (db > 0):
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt((concordant + discordant + ties_a) * (concordant + discordant + ties_b))
    if denominator == 0:
        return None
    return (concordant - discordant) / denominator
