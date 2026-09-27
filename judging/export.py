"""CSV export at every stage. Each function returns (filename, rows) where the
first row is the header, so the API and the UI share one code path."""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata

from events.models import Event, Project, TeamMembership

from .models import JudgeAssignment, JudgeCalibration, ProjectResult
from .services import ensure_rubric

RUNS_AS_FORMULA = ("=", "+", "-", "@")
# -5, +5, 1,5, -1,234.56, -1e-05, -.5, -5%: a number however it is written.
PLAIN_NUMBER = re.compile(r"^(?=.*[0-9])[+-]?([0-9]{1,3}(,[0-9]{3})+|[0-9]*)([.,][0-9]+)?([eE][+-]?[0-9]+)?%?$")


def _as_a_spreadsheet_reads_it(text: str) -> str:
    """Full-width signs folded to the plain ones, and whatever shows as
    nothing in front taken off: spreadsheets do both before deciding whether
    a cell is a formula."""
    folded = unicodedata.normalize("NFKC", text)
    start = 0
    while start < len(folded) and (
        folded[start].isspace() or unicodedata.category(folded[start]) in ("Cf", "Cc", "Zs", "Zl", "Zp")
    ):
        start += 1
    return folded[start:]


def safe_cell(value):
    """A team may call itself =HYPERLINK(...). An organizer opening the export
    in a spreadsheet must see that as text, not have it run. Cells that would
    start a formula get a leading apostrophe, which says "this is text".
    Numbers are left alone.

    The price, paid knowingly: a phone number written +82-10-..., a handle
    written @name and a comment that opens with a dash carry the apostrophe
    too, and a reader who opens the file as plain text sees it."""
    if isinstance(value, (dict, list)):
        return safe_cell(json.dumps(value, ensure_ascii=False, default=str))
    if not isinstance(value, str) or not value:
        return value
    if value[0] in ("\t", "\r"):
        return "'" + value
    read = _as_a_spreadsheet_reads_it(value)
    if PLAIN_NUMBER.match(read):
        return value
    if read.startswith(RUNS_AS_FORMULA):
        return "'" + value
    return value


def joined(parts) -> str:
    """Several values in one cell. Each is made safe by itself: a program
    that splits the cell on the semicolon must not find a formula behind it."""
    return ";".join(str(safe_cell(str(p))) for p in parts)


def md_cell(value) -> str:
    """Text for one cell of a Markdown table: a title with a bar or a line
    break in it must not add a column or a row."""
    return " ".join(str(value).split()).replace("\\", "\\\\").replace("|", "\\|")


def to_csv(rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for n, row in enumerate(rows):
        writer.writerow(row if n == 0 else [safe_cell(c) for c in row])
    return buf.getvalue()


def projects_csv(event: Event) -> tuple[str, list[list]]:
    rows = [
        [
            "project_id",
            "external_id",
            "title",
            "tagline",
            "status",
            "hidden",
            "duplicate_of",
            "team",
            "track",
            "repo_url",
            "live_url",
            "demo_video_url",
            "tech_tags",
            "submitted_at",
            "members",
        ]
    ]
    members = {}
    for m in TeamMembership.objects.filter(team__event=event).select_related("user"):
        members.setdefault(m.team_id, []).append(m.user.email or m.user.username)
    for p in Project.objects.filter(event=event).select_related("team", "track", "duplicate_of").order_by("id"):
        rows.append(
            [
                p.id,
                p.external_id,
                p.title,
                p.tagline,
                p.status,
                p.is_hidden,
                p.duplicate_of_id or "",
                p.team.name,
                p.track.name if p.track else "",
                p.repo_url,
                p.live_url,
                p.demo_video_url,
                joined(p.tech_tags or []),
                p.submitted_at.isoformat() if p.submitted_at else "",
                joined(members.get(p.team_id, [])),
            ]
        )
    return f"{event.slug}-projects.csv", rows


def assignments_csv(event: Event) -> tuple[str, list[list]]:
    rows = [
        ["assignment_id", "batch", "judge", "judge_email", "project_id", "project", "track", "status", "submitted_at"]
    ]
    qs = (
        JudgeAssignment.objects.filter(event=event)
        .select_related("judge", "project", "project__track")
        .order_by("batch", "judge__username", "id")
    )
    for a in qs:
        rows.append(
            [
                a.id,
                a.batch,
                a.judge.username,
                a.judge.email,
                a.project_id,
                a.project.title,
                a.project.track.name if a.project.track else "",
                a.status,
                a.submitted_at.isoformat() if a.submitted_at else "",
            ]
        )
    return f"{event.slug}-assignments.csv", rows


def scores_csv(event: Event) -> tuple[str, list[list]]:
    rubric = ensure_rubric(event)
    criteria = list(rubric.criteria.all())
    rows = [
        ["assignment_id", "judge", "judge_email", "project_id", "project", "track", "status"]
        + [c.key for c in criteria]
        + ["comment"]
    ]
    qs = (
        JudgeAssignment.objects.filter(event=event)
        .select_related("judge", "project", "project__track")
        .prefetch_related("scores__criterion")
        .order_by("project_id", "judge__username")
    )
    for a in qs:
        values = {s.criterion_id: s.value for s in a.scores.all()}
        rows.append(
            [
                a.id,
                a.judge.username,
                a.judge.email,
                a.project_id,
                a.project.title,
                a.project.track.name if a.project.track else "",
                a.status,
            ]
            + [values.get(c.id, "") for c in criteria]
            + [a.comment]
        )
    return f"{event.slug}-scores.csv", rows


def results_csv(event: Event) -> tuple[str, list[list]]:
    rows = [
        [
            "rank",
            "rank_normalized",
            "rank_raw",
            "project_id",
            "project",
            "track",
            "team",
            "reviews",
            "raw_mean",
            "normalized_mean",
            "adjusted_mean",
            "method",
        ]
    ]
    qs = ProjectResult.objects.filter(event=event).select_related("project", "project__track", "project__team")
    for r in qs:
        rows.append(
            [
                r.rank or "",
                r.rank_normalized or "",
                r.rank_raw or "",
                r.project_id,
                r.project.title,
                r.project.track.name if r.project.track else "",
                r.project.team.name,
                r.review_count,
                f"{r.raw_mean:.4f}" if r.raw_mean is not None else "",
                f"{r.normalized_mean:.4f}" if r.normalized_mean is not None else "",
                f"{r.adjusted_mean:.4f}" if r.adjusted_mean is not None else "",
                r.method,
            ]
        )
    return f"{event.slug}-results.csv", rows


def calibration_csv(event: Event) -> tuple[str, list[list]]:
    rows = [["judge", "judge_email", "reviews", "mean", "stdev", "shrink_weight", "flat"]]
    for c in JudgeCalibration.objects.filter(event=event).select_related("judge").order_by("judge__username"):
        rows.append(
            [
                c.judge.username,
                c.judge.email,
                c.review_count,
                f"{c.mean:.4f}" if c.mean is not None else "",
                f"{c.stdev:.4f}" if c.stdev is not None else "",
                f"{c.shrink_weight:.4f}" if c.shrink_weight is not None else "",
                c.flat,
            ]
        )
    return f"{event.slug}-judge-calibration.csv", rows


def audit_csv(event: Event) -> tuple[str, list[list]]:
    rows = [["at", "actor", "action", "target_type", "target_id", "target", "channel", "ip", "detail"]]
    for e in event.audit_entries.all().order_by("created_at", "id"):
        rows.append(
            [
                e.created_at.isoformat(),
                e.actor_label,
                e.action,
                e.target_type,
                e.target_id,
                e.target_label,
                e.channel,
                e.ip_address or "",
                e.detail,
            ]
        )
    return f"{event.slug}-audit.csv", rows


EXPORTS = {
    "projects": projects_csv,
    "assignments": assignments_csv,
    "scores": scores_csv,
    "results": results_csv,
    "calibration": calibration_csv,
    "audit": audit_csv,
}
