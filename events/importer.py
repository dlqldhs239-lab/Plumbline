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
MAX_CELL = 20_000

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
    """Rows as dictionaries, read by position so that a repeated or empty
    header cell cannot shift one column's values into another."""
    if kind not in COLUMNS:
        raise ValidationError("Import either projects or judges.")
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValidationError("The file is larger than 2 MB. Split it.")
    text = text.lstrip("﻿")
    if "\x00" in text:
        raise ValidationError("The file is not text.")
    # The separator is whichever of comma, semicolon and tab the header line
    # uses most. Spreadsheets in many countries write semicolons.
    header = text.strip().splitlines()[0] if text.strip() else ""
    delimiter = max(",;\t", key=header.count)
    csv.field_size_limit(MAX_BYTES)
    rows, names = [], None
    try:
        for cells in csv.reader(io.StringIO(text), delimiter=delimiter):
            if not any((c or "").strip() for c in cells):
                continue
            if any(len(c) > MAX_CELL for c in cells):
                raise ValidationError(f"A cell is longer than {MAX_CELL} characters.")
            if names is None:
                names = [(c or "").strip().lower().replace(" ", "_") for c in cells]
                continue
            if len(cells) > len(names) and any(c.strip() for c in cells[len(names) :]):
                raise ValidationError(
                    f"Line {len(rows) + 2} has more cells than the first line has columns. "
                    "A value with a comma in it needs quotes around it."
                )
            rows.append({n: (cells[i].strip() if i < len(cells) else "") for i, n in enumerate(names) if n})
            if len(rows) > MAX_ROWS:
                raise ValidationError(f"More than {MAX_ROWS} rows. Split the file.")
    except csv.Error as e:
        raise ValidationError(f"The file could not be read as CSV: {e}") from None
    if names is None:
        raise ValidationError("The file is empty.")
    named = [n for n in names if n]
    repeated = sorted({n for n in named if named.count(n) > 1})
    if repeated:
        raise ValidationError(f"The first line names a column twice: {', '.join(repeated)}.")
    missing = [c for c in REQUIRED[kind] if c not in named]
    if missing:
        raise ValidationError(
            f"The first line must name the columns. Missing: {', '.join(missing)}. "
            f"Known columns: {', '.join(COLUMNS[kind])}."
        )
    ignored = [n for n in named if n not in COLUMNS[kind]]
    if any(not n for n in names):
        ignored.append("a column without a name")
    return rows, ignored


def _email(value: str, problems: list[str]) -> bool:
    try:
        validate_email(value)
    except ValidationError:
        problems.append(f"'{value[:60]}' is not an email address")
        return False
    if len(value) > 254:
        problems.append("an email address is longer than 254 characters")
        return False
    return True


def plan(event: Event, user, text: str, kind: str) -> dict:
    """What the import would do. Writes nothing."""
    if not is_organizer(user, event):
        raise PermissionDenied("Only organizers can import.")
    rows, ignored = _read(text, kind)
    tracks = {t.name.strip().lower(): t for t in event.tracks.all()}
    User = get_user_model()
    warnings = []
    if kind == "projects":
        if event.results_published:
            warnings.append("The results of this event are published. Imported projects will not be in them.")
        elif event.submissions_closed():
            warnings.append("Submissions have closed. Imported projects arrive as submitted now, after the deadline.")
        required = event.custom_questions.filter(required=True).count()
        if required:
            warnings.append(
                f"The submission form has {required} required question{'s' if required != 1 else ''} of your own. "
                "Imported projects arrive without answers to them."
            )

    teams_by_name: dict[str, list[Team]] = {}
    for t in Team.objects.filter(event=event):
        teams_by_name.setdefault(t.name.lower(), []).append(t)
    member_of = {
        (e or "").lower(): (team_id, name)
        for e, team_id, name in TeamMembership.objects.filter(team__event=event).values_list(
            "user__email", "team_id", "team__name"
        )
        if e
    }
    roles: dict[str, set[str]] = {}
    for e, role in EventRole.objects.filter(event=event).values_list("user__email", "role"):
        if e:
            roles.setdefault(e.lower(), set()).add(role)
    existing_titles = {
        (team_id, title.lower())
        for team_id, title in Project.objects.filter(event=event).values_list("team_id", "title")
    }

    out, seen = [], set()
    placed_in: dict[str, str] = {}  # member -> team name, inside this file
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
            matches = teams_by_name.get(team.lower(), [])
            if len(matches) > 1:
                problems.append(f"{len(matches)} teams in this event are called '{team}'; rename one of them first")
            found = matches[0] if len(matches) == 1 else None
            if found is not None:
                notes.append(f"joins the existing team {found.name}")
            key = (team.lower(), title.lower())
            if team and title and key in seen:
                problems.append("the same team and title appear on an earlier line")
            if found is not None and (found.id, title.lower()) in existing_titles:
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
            members = list(dict.fromkeys(m.lower() for m in _split(row.get("members", ""))))
            new_people = known = 0
            for m in members:
                if not _email(m, problems):
                    continue
                on_team = member_of.get(m)
                if on_team and (found is None or on_team[0] != found.id):
                    problems.append(f"{m} is already on team {on_team[1]}")
                elif placed_in.get(m, team.lower()) != team.lower():
                    problems.append(f"{m} is on another team earlier in this file")
                placed_in.setdefault(m, team.lower())
                held = roles.get(m, set())
                if Role.JUDGE in held:
                    problems.append(f"{m} is a judge of this event and cannot be on a team in it")
                if Role.ORGANIZER in held:
                    problems.append(f"{m} is an organizer of this event")
                person = User.objects.filter(email__iexact=m).order_by("id").first()
                if person is None:
                    new_people += 1
                elif person.is_staff or person.is_superuser:
                    problems.append(f"{m} is a staff account; add staff to teams by hand")
                else:
                    known += 1
            if new_people:
                notes.append(f"{new_people} new account{'s' if new_people != 1 else ''}")
            if known:
                notes.append(f"{known} existing account{'s' if known != 1 else ''} will be added to the team")
            if not members:
                notes.append("no members listed")
            what = f"{title} by {team}"
        else:
            email = row.get("email", "").lower()
            _email(email, problems)
            if email in seen:
                problems.append("the same address appears on an earlier line")
            seen.add(email)
            if len(row.get("name", "")) > 150:
                problems.append("name longer than 150 characters")
            for t in _split(row.get("tracks", "")):
                if t.lower() not in tracks:
                    problems.append(f"no track called '{t}' in this event")
            if email in member_of:
                problems.append(f"{email} is on team {member_of[email][1]} in this event and cannot judge it")
            if Role.JUDGE in roles.get(email, set()):
                notes.append("already a judge here: tracks will be updated")
            elif email and not User.objects.filter(email__iexact=email).exists():
                notes.append("new account")
            elif email:
                notes.append("existing account")
            what = row.get("name") or email
        out.append({"line": n, "what": what, "problems": problems, "notes": notes, "row": row, "ok": not problems})
    bad = sum(1 for r in out if not r["ok"])
    return {
        "kind": kind,
        "rows": out,
        "total": len(out),
        "bad": bad,
        "ready": bool(out) and bad == 0,
        "ignored_columns": ignored,
        "warnings": warnings,
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

    def account(email: str):
        found = User.objects.filter(email__iexact=email).order_by("id").first()
        if found:
            return found
        base = username = email.split("@")[0][:140] or "user"
        i = 2
        while User.objects.filter(username=username).exists():
            username, i = f"{base}{i}", i + 1
        person = User(username=username, email=email)
        person.set_unusable_password()
        person.save()
        made["accounts"] += 1
        return person

    if kind == "judges":
        before = User.objects.count()
        for r in checked["rows"]:
            row = r["row"]
            services.add_judge(
                event, user, row["email"], row.get("name", ""), _track_list(event, row.get("tracks", ""))
            )
            made["judges"] += 1
        made["accounts"] = User.objects.count() - before
    else:
        tracks = {t.name.strip().lower(): t for t in event.tracks.all()}
        teams = {t.name.lower(): t for t in Team.objects.filter(event=event)}
        for r in checked["rows"]:
            row = r["row"]
            team = teams.get(row["team"].lower())
            if team is None:
                team = Team(event=event, name=row["team"], created_by=user)
                team.full_clean(exclude=["external_id"])
                team.save()
                teams[row["team"].lower()] = team
                made["teams"] += 1
            for email in dict.fromkeys(m.lower() for m in _split(row.get("members", ""))):
                person = account(email)
                if TeamMembership.objects.filter(team__event=event, user=person).exclude(team=team).exists():
                    raise ValidationError(f"{email} is already on another team in this event; nothing was imported.")
                first = not team.memberships.exists()
                role = TeamMembership.MemberRole.OWNER if first else TeamMembership.MemberRole.MEMBER
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
