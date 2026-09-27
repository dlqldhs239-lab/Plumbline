"""What the public pages show about judging: the landing page's plumb lines
and its opening case, and the geometry of the results slopegraph.

Everything here reads published results only. Nothing is invented: if an
installation has no published event, the functions say so and the pages fall
back to plain copy.
"""

from __future__ import annotations

from django.db.models import Count

from events.models import Event

from .models import JudgeCalibration, ProjectResult
from .services import ensure_rubric, placed

SMALL = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
ORDINALS = (
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth"
).split()
ORDINAL_TENS = "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split()


def in_words(n: int) -> str:
    """Whole numbers under a hundred as words, as running text wants them."""
    if n < 0 or n > 99:
        return str(n)
    if n < 20:
        return SMALL[n]
    tens, ones = divmod(n, 10)
    return TENS[tens - 2] + (f"-{SMALL[ones]}" if ones else "")


def ordinal(n: int) -> str:
    """Places as running text wants them: twelfth, twenty-sixth, 104th."""
    if 0 < n < 20:
        return ORDINALS[n]
    if 20 <= n < 100:
        tens, ones = divmod(n, 10)
        return ORDINAL_TENS[tens - 2] if not ones else f"{TENS[tens - 2]}-{ORDINALS[ones]}"
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def featured_event(user=None) -> Event | None:
    """The listed event with published results and the most judging behind it."""
    return (
        Event.objects.filter(is_listed=True, results_published_at__isnull=False)
        .annotate(n=Count("results"))
        .filter(n__gt=0)
        .order_by("-n", "-results_published_at")
        .first()
    )


def panel(event: Event) -> dict:
    """The judges of an event as the landing page draws them: one entry per
    judge, in a stable order, carrying no name or id."""
    rows = [
        r
        for r in JudgeCalibration.objects.filter(event=event).order_by("judge_id")
        if r.mean is not None and r.review_count
    ]
    reviews = sum(r.review_count for r in rows)
    mean = sum(r.mean * r.review_count for r in rows) / reviews if reviews else None
    judges = [
        {
            "n": r.review_count,
            "lean": round(r.mean - mean, 4),
            "mean": round(r.mean, 3),
            "spread": round(r.stdev or 0.0, 3),
            "flat": r.flat,
        }
        for r in rows
    ]
    # Interleave generous and harsh so the field does not read as a sorted chart.
    judges.sort(key=lambda j: (j["n"], j["lean"]))
    judges = judges[::2] + judges[1::2][::-1]
    return {
        "judges": judges,
        "mean": round(mean, 3) if mean is not None else None,
        "reviews": reviews,
        "max_lean": max((abs(j["lean"]) for j in judges), default=0.0),
        "max_n": max((j["n"] for j in judges), default=0),
    }


def case(event: Event) -> dict | None:
    """The one story the landing page opens with, chosen from the data.

    First choice: a judge who gave every project the same mark, and the
    project that mark was holding up. Otherwise the project that moved the
    most between the raw order and the final one.
    """
    ranked = [r for r in placed(event) if r.place and r.place_raw]
    if not ranked or not panel(event)["judges"]:
        return None
    rubric = ensure_rubric(event)
    total = len(ranked)
    moved = sum(1 for r in ranked if r.shift)
    biggest = max(ranked, key=lambda r: (abs(r.shift), -r.place))
    out = {
        "event": event,
        "projects": total,
        "moved": moved,
        "scale_max": rubric.scale_max,
        "judges": JudgeCalibration.objects.filter(event=event).count(),
        "project": biggest,
        "from_rank": biggest.place_raw,
        "to_rank": biggest.place,
        "from_word": ordinal(biggest.place_raw),
        "to_word": ordinal(biggest.place),
        "kind": "mover",
    }
    flat = JudgeCalibration.objects.filter(event=event, flat=True).order_by("-review_count").first()
    if flat is not None:
        from .models import JudgeAssignment, Score

        theirs = JudgeAssignment.objects.filter(
            event=event, judge_id=flat.judge_id, status=JudgeAssignment.Status.SUBMITTED
        )
        # The headline says "gave everyone a four". That is only true if every
        # single mark was that number, not merely every weighted total.
        marks = set(Score.objects.filter(assignment__in=theirs).values_list("value", flat=True))
        held = [r for r in ranked if r.project_id in set(theirs.values_list("project_id", flat=True))]
        if held and len(marks) == 1:
            worst = max(held, key=lambda r: r.place - r.place_raw)
            if worst.place > worst.place_raw:
                mark = float(marks.pop())
                out.update(
                    {
                        "kind": "flat",
                        "mark": int(mark) if float(mark).is_integer() else round(mark, 2),
                        "mark_word": in_words(int(mark)) if float(mark).is_integer() else f"{mark:.2f}",
                        "flat_reviews": flat.review_count,
                        "flat_reviews_word": in_words(flat.review_count),
                        "project": worst,
                        "from_rank": worst.place_raw,
                        "to_rank": worst.place,
                        "from_word": ordinal(worst.place_raw),
                        "to_word": ordinal(worst.place),
                    }
                )
    out["judges_word"] = in_words(out["judges"]).capitalize()
    return out


SLOPE_LIMIT = 40
SLOPE_ROW = 20  # pixels per project; the labels and the lines share it
SLOPE_WIDTH = 600


def slopegraph(rows: list[ProjectResult], limit: int = SLOPE_LIMIT) -> dict | None:
    """Left: the order raw means gave. Right: the final order. One line per
    project. Drawn for the first `limit` projects of the final order; the
    left column ranks the same projects among themselves."""
    shown = [r for r in rows if r.rank and r.rank_raw and r.raw_mean is not None][:limit]
    if len(shown) < 2:
        return None
    left = sorted(shown, key=lambda r: (-round(r.raw_mean, 9), r.rank, r.project_id))
    left_pos = {r.project_id: i for i, r in enumerate(left)}
    height = len(shown) * SLOPE_ROW
    lines = []
    for i, r in enumerate(shown):
        y0 = left_pos[r.project_id] * SLOPE_ROW + SLOPE_ROW / 2
        y1 = i * SLOPE_ROW + SLOPE_ROW / 2
        delta = left_pos[r.project_id] - i  # positive: moved up
        lines.append(
            {
                "id": r.project_id,
                "d": f"M0 {y0:g} C {SLOPE_WIDTH * 0.37:g} {y0:g}, {SLOPE_WIDTH * 0.63:g} {y1:g}, {SLOPE_WIDTH} {y1:g}",
                "delta": delta,
                "tone": "held" if abs(delta) < 3 else ("up" if delta > 0 else "down"),
            }
        )
    # Quiet lines first, so the ones that matter are painted on top.
    lines.sort(key=lambda line: abs(line["delta"]))
    return {
        "left": left,
        "right": shown,
        "lines": lines,
        "height": height,
        "width": SLOPE_WIDTH,
        "row": SLOPE_ROW,
        "shown": len(shown),
        "total": len([r for r in rows if r.rank]),
    }


ELEV_W, ELEV_H = 1200, 420
ELEV_LEFT, ELEV_RIGHT, ELEV_TOP, ELEV_BOTTOM = 64, 150, 28, 56


def elevation(event: Event) -> dict | None:
    """The panel drawn as an elevation, for organizers. The datum is the panel
    mean. Each post is a judge: it rises or falls from the datum to that
    judge's mean, and the bracket is one standard deviation either side.
    Judges stand in order of their mean, harshest first."""
    from events.models import EventRole, Role

    rows = [
        r
        for r in JudgeCalibration.objects.filter(event=event).select_related("judge")
        if r.mean is not None and r.review_count
    ]
    if len(rows) < 2:
        return None
    rubric = ensure_rubric(event)
    reviews = sum(r.review_count for r in rows)
    datum = sum(r.mean * r.review_count for r in rows) / reviews
    labels = dict(
        EventRole.objects.filter(event=event, role=Role.JUDGE)
        .exclude(external_id="")
        .values_list("user_id", "external_id")
    )
    low = max(rubric.scale_min, min(min(r.mean - (r.stdev or 0) for r in rows), datum) - 0.2)
    high = min(rubric.scale_max, max(max(r.mean + (r.stdev or 0) for r in rows), datum) + 0.2)
    span = (high - low) or 1
    plot_h = ELEV_H - ELEV_TOP - ELEV_BOTTOM

    def y(value: float) -> float:
        value = max(low, min(high, value))
        return round(ELEV_TOP + (high - value) / span * plot_h, 1)

    rows.sort(key=lambda r: (r.mean, r.judge_id))
    step = (ELEV_W - ELEV_LEFT - ELEV_RIGHT - 60) / (len(rows) - 1)
    most = max(r.review_count for r in rows)
    posts = []
    for i, r in enumerate(rows):
        name = r.judge.get_full_name() or r.judge.username
        sd = r.stdev or 0.0
        lean = r.mean - datum
        posts.append(
            {
                "x": round(ELEV_LEFT + 20 + i * step, 1),
                "y": y(r.mean),
                "hi": y(r.mean + sd),
                "lo": y(r.mean - sd),
                "spread": sd > 0,
                "flat": r.flat,
                "thin": r.review_count < 3,
                "width": round(1 + 2.4 * r.review_count / most, 2),
                "label": (labels.get(r.judge_id) or name)[-2:] if labels.get(r.judge_id) else str(i + 1),
                "name": name,
                "n": r.review_count,
                "mean": r.mean,
                "stdev": sd,
                "lean": lean,
                "tip": (
                    f"{name}: {r.review_count} review{'s' if r.review_count != 1 else ''}, mean {r.mean:.2f} "
                    f"({lean:+.2f} from the panel), spread {sd:.2f}"
                    + (". Every score the same: counts as no opinion." if r.flat else "")
                ),
            }
        )
    first = int(low) if low == int(low) else int(low) + 1
    grid = [{"value": v, "y": y(v)} for v in range(first, int(high) + 1)]
    return {
        "width": ELEV_W,
        "height": ELEV_H,
        "left": ELEV_LEFT,
        "right": ELEV_W - ELEV_RIGHT,
        "base": ELEV_H - ELEV_BOTTOM + 28,
        "datum": {"value": datum, "y": y(datum)},
        "grid": grid,
        "posts": posts,
        "judges": len(posts),
        "flat": sum(1 for q in posts if q["flat"]),
        "thin": sum(1 for q in posts if q["thin"]),
        "harshest": posts[0],
        "kindest": posts[-1],
        "range": posts[-1]["mean"] - posts[0]["mean"],
    }
