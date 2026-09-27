"""Bulk import from CSV: projects with their teams, and judges.

An organizer moving an event here from a spreadsheet or another platform
should not retype it. The import is in two steps. `plan` reads the file and
says, row by row, what it would do and what is wrong, and writes nothing.
`apply` does it, all of it or none of it.
"""

from __future__ import annotations

import csv
import io

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from audit.services import record

from . import services
from .models import Event, EventRole, Project, Role, Team, TeamMembership
from .permissions import is_organizer

MAX_ROWS = 2000
MAX_BYTES = 2_000_000

PROJECT_COLUMNS = (
    "team",
    "title",
    "tagline",
    "description",
    "track",
    "repo_url",
    "live_url",
    "demo_video_url",
    "tags",
    "members",
)
JUDGE_COLUMNS = ("email", "name", "tracks")
REQUIRED = {"projects": ("team", "title"), "judges": ("email",)}
COLUMNS = {"projects": PROJECT_COLUMNS, "judges": JUDGE_COLUMNS}


def template(kind: str) -> str:
    """A file with the header and one example row, to fill in."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS[kind])
    if kind == "projects":
        w.writerow(
            [
                "Nightshift",
                "Quiet Hours",
                "Mutes the pager after midnight",
                "",
                "Developer tools",
                "https://example.org/repo",
                "",
                "",
                "django; postgres",
                "ana@example.org; ben@example.org",
            ]
        )
    else:
        w.writerow(["judge@example.org", "Jo Judge", "Developer tools; Security"])
    return out.getvalue()


def _split(value: str) -> list[str]:
    return [p.strip() for p in (value or "").replace(",", ";").split(";") if p.strip()]


def _read(text: str, kind: str) -> tuple[list[dict], list[str]]:
    if kind not in COLUMNS:
        raise ValidationError("Import either projects or judges.")
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValidationError("The file is larger than 2 MB. Split it.")
    text = text.lstrip("﻿")
    # The separator is whichever of comma, semicolon and tab the header line
    # uses most. Spreadsheets in many countries write semicolons.
    header = text.strip().splitlines()[0] if text.strip() else ""
    delimiter = max(",;	", key=header.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValidationError("The file is empty.")
    names = [(n or "").strip().lower().replace(" ", "_") for n in reader.fieldnames]
    missing = [c for c in REQUIRED[kind] if c not in names]
    if missing:
        raise ValidationError(
            f"The first line must name the columns. Missing: {', '.join(missing)}. "
            f"Known columns: {', '.join(COLUMNS[kind])}."
        )
    ignored = [n for n in names if n and n not in COLUMNS[kind]]
    rows = []
    try:
        for raw in reader:
            row = {k: (v or "").strip() for k, v in zip(names, raw.values(), strict=False) if k and isinstance(v, str)}
            if any(row.values()):
                rows.append(row)
            if len(rows) > MAX_ROWS:
                raise ValidationError(f"More than {MAX_ROWS} rows. Split the file.")
    except csv.Error as e:
        raise ValidationError(f"The file could not be read as CSV: {e}") from None
    return rows, ignored


def plan(event: Event, user, text: str, kind: str) -> dict:
    """What the import would do. Writes nothing."""
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can import.")
    rows, ignored = _read(text, kind)
    tracks = {t.name.strip().lower(): t for t in event.tracks.all()}
    User = get_user_model()
    out = []
    seen = set()
    member_team: dict[str, str] = {}
    existing_titles = {
        (t.lower(), n.lower()) for t, n in Project.objects.filter(event=event).values_list("team__name", "title")
    }
    existing_members = {
        e.lower(): t
        for e, t in TeamMembership.objects.filter(team__event=event).values_list("user__email", "team__name")
        if e
    }
    existing_judges = set(
        e.lower()
        for e in EventRole.objects.filter(event=event, role=Role.JUDGE).values_list("user__email", flat=True)
        if e
    )
    for n, row in enumerate(rows, start=2):  # line 1 is the header
        problems, notes = [], []
        if kind == "projects":
            team, title = row.get("team", ""), row.get("title", "")
            if not team:
                problems.append("no team")
            if not title:
                problems.append("no title")
            if len(team) > 120:
                problems.append("team name longer than 120 characters")
            if len(title) > 200:
                problems.append("title longer than 200 characters")
            if len(row.get("tagline", "")) > 300:
                problems.append("tagline longer than 300 characters")
            key = (team.lower(), title.lower())
            if team and title and key in seen:
                problems.append("the same team and title appear on an earlier line")
            if team and title and key in existing_titles:
                problems.append("this team already has a project with this title in the event")
            seen.add(key)
            track = row.get("track", "")
            if track and track.lower() not in tracks:
                problems.append(f"no track called '{track}' in this event")
            if not track and tracks:
                notes.append("no track")
            for field, label in (
                ("repo_url", "Repository"),
                ("live_url", "Live address"),
                ("demo_video_url", "Demo video"),
            ):
                try:
                    services._web_address(row.get(field, ""), label)
                except ValidationError as e:
                    problems.append(e.messages[0])
            tags = _split(row.get("tags", ""))
            if len(tags) > 20 or any(len(t) > 40 for t in tags):
                problems.append("at most 20 tags, each at most 40 characters")
            members = [m.lower() for m in _split(row.get("members", ""))]
            for m in members:
                try:
                    validate_email(m)
                except ValidationError:
                    problems.append(f"'{m}' is not an email address")
                    continue
                other = member_team.get(m) or existing_members.get(m)
                if other and other.lower() != team.lower():
                    problems.append(f"{m} is already on team {other}")
                member_team.setdefault(m, team)
            new_people = [m for m in members if not User.objects.filter(email__iexact=m).exists()]
            if new_people:
                notes.append(f"{len(new_people)} new account{'s' if len(new_people) != 1 else ''}")
            if not members:
                notes.append("no members listed")
            out.append(
                {
                    "line": n,
                    "what": f"{title} by {team}",
                    "problems": problems,
                    "notes": notes,
                    "row": row,
                    "ok": not problems,
                }
            )
        else:
            email = row.get("email", "").lower()
            try:
                validate_email(email)
            except ValidationError:
                problems.append("not an email address")
            if email in seen:
                problems.append("the same address appears on an earlier line")
            seen.add(email)
            if len(row.get("name", "")) > 150:
                problems.append("name longer than 150 characters")
            for t in _split(row.get("tracks", "")):
                if t.lower() not in tracks:
                    problems.append(f"no track called '{t}' in this event")
            if email in existing_judges:
                notes.append("already a judge here: tracks will be updated")
            elif email and not User.objects.filter(email__iexact=email).exists():
                notes.append("new account")
            out.append(
                {
                    "line": n,
                    "what": row.get("name") or email,
                    "problems": problems,
                    "notes": notes,
                    "row": row,
                    "ok": not problems,
                }
            )
    bad = sum(1 for r in out if not r["ok"])
    return {
        "kind": kind,
        "rows": out,
        "total": len(out),
        "bad": bad,
        "ready": bool(out) and bad == 0,
        "ignored_columns": ignored,
    }


@transaction.atomic
def apply(event: Event, user, text: str, kind: str) -> dict:
    """Do what `plan` said. Refused as a whole if any row has a problem."""
    checked = plan(event, user, text, kind)
    if not checked["rows"]:
        raise ValidationError("The file has no rows.")
    if checked["bad"]:
        first = next(r for r in checked["rows"] if not r["ok"])
        raise ValidationError(
            f"{checked['bad']} row{'s have' if checked['bad'] != 1 else ' has'} problems; nothing was imported. "
            f"Line {first['line']}: {first['problems'][0]}."
        )
    User = get_user_model()
    made = {"projects": 0, "teams": 0, "accounts": 0, "judges": 0}

    def account(email: str, name: str = ""):
        found = User.objects.filter(email__iexact=email).order_by("id").first()
        if found:
            return found
        base = username = email.split("@")[0][:140] or "user"
        i = 2
        while User.objects.filter(username=username).exists():
            username, i = f"{base}{i}", i + 1
        person = User(username=username, email=email)
        person.set_unusable_password()
        if name:
            first, _, last = name.partition(" ")
            person.first_name, person.last_name = first[:150], last[:150]
        person.save()
        made["accounts"] += 1
        return person

    if kind == "judges":
        for r in checked["rows"]:
            row = r["row"]
            services.add_judge(
                event, user, row["email"], row.get("name", ""), _track_list(event, row.get("tracks", ""))
            )
            made["judges"] += 1
        made["accounts"] = 0  # add_judge creates them; counted by the audit entries it writes
    else:
        tracks = {t.name.strip().lower(): t for t in event.tracks.all()}
        teams = {t.name.lower(): t for t in Team.objects.filter(event=event)}
        for r in checked["rows"]:
            row = r["row"]
            team = teams.get(row["team"].lower())
            if team is None:
                team = Team.objects.create(event=event, name=row["team"], created_by=user)
                teams[row["team"].lower()] = team
                made["teams"] += 1
            for n, email in enumerate(m.lower() for m in _split(row.get("members", ""))):
                person = account(email)
                role = (
                    TeamMembership.MemberRole.OWNER
                    if n == 0 and not team.memberships.exists()
                    else TeamMembership.MemberRole.MEMBER
                )
                TeamMembership.objects.get_or_create(team=team, user=person, defaults={"role": role})
                EventRole.objects.get_or_create(event=event, user=person, role=Role.PARTICIPANT)
            project = Project(event=event, team=team, created_by=user)
            services._apply(
                project,
                {
                    "title": row["title"],
                    "tagline": row.get("tagline", ""),
                    "description": row.get("description", ""),
                    "repo_url": row.get("repo_url", ""),
                    "live_url": row.get("live_url", ""),
                    "demo_video_url": row.get("demo_video_url", ""),
                    "tech_tags": _split(row.get("tags", "")),
                    "track": tracks.get(row.get("track", "").lower()),
                },
            )
            project.status = Project.Status.SUBMITTED
            project.submitted_at = timezone.now()
            project.full_clean(exclude=["external_id"])
            project.save()
            made["projects"] += 1
    record("import." + kind, actor=user, event=event, detail={**made, "rows": checked["total"]})
    return made


def _track_list(event: Event, value: str):
    tracks = {t.name.strip().lower(): t for t in event.tracks.all()}
    return [tracks[t.lower()] for t in _split(value)]
