"""Load the organizer's fixtures.json into the portal.

Idempotent: keyed on the fixture ids, so `docker compose up` twice does not
double the data. The event's submissions_close comes from the file, which is
why the closed-event check passes without any clock tricks.

Prints the four auth headers for .dogfood.toml at the end.
"""

from __future__ import annotations

import json
from datetime import UTC, timedelta
from pathlib import Path

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from accounts.models import ApiToken
from audit.services import record
from events.models import Event, EventRole, Project, Role, Team, TeamMembership, Track
from events.services import RESERVED_SLUGS
from judging.models import Criterion, JudgeAssignment, Rubric, Score

ORGANIZER_EMAIL = "organizer@example.org"
ADMIN_EMAIL = "admin@example.org"
TOKEN_SLOTS = {
    "organizer": ("org", ORGANIZER_EMAIL),
    "judge_a": ("jdg_a", None),  # filled from fixtures below
    "judge_b": ("jdg_b", None),
    "participant": ("prt", None),
}


SEED_PASSWORD = "plumbline"
_password_hash_cache: dict[str, str] = {}


def _password_hash(password: str) -> str:
    """Hash once, reuse for every seeded user: PBKDF2 per user would make the
    seed take a minute for 170 accounts."""
    if password not in _password_hash_cache:
        _password_hash_cache[password] = make_password(password)
    return _password_hash_cache[password]


def _user_for(email: str, name: str = "", password: str | None = None, **flags) -> User:
    """The account for a fixture email, created if it is missing.

    An account that already exists is returned as it is. The seed runs on
    every start, and it must never rename a person or hand staff rights to
    whoever happens to hold admin@example.org by then.
    """
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).order_by("id").first()
    if user is not None:
        return user
    username = email.split("@")[0][:140] or "user"
    base = username
    i = 2
    while User.objects.filter(username=username).exists():
        username = f"{base}{i}"
        i += 1
    user = User(username=username, email=email)
    user.password = _password_hash(password or SEED_PASSWORD)
    if name:
        parts = name.split(" ", 1)
        user.first_name = parts[0][:150]
        user.last_name = parts[1][:150] if len(parts) > 1 else ""
    for k, v in flags.items():
        setattr(user, k, v)
    user.save()
    return user


def _free_slug(wanted: str) -> str:
    """A slug nobody uses yet: an organizer may have taken the fixture's name
    for an event of their own before the seed first ran."""
    base = (wanted or "event")[:70]
    slug, i = base, 2
    while slug in RESERVED_SLUGS or Event.objects.filter(slug=slug).exists():
        slug, i = f"{base}-{i}", i + 1
    return slug


class Command(BaseCommand):
    help = "Load DOGFOOD fixtures.json (idempotent) and print the checker auth headers."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default="fixtures.json")
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"fixtures file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        with transaction.atomic():
            headers = self.load(data)
        if not options["quiet"]:
            self.stdout.write("")
            self.stdout.write("[plumbline] seed complete. Auth headers for .dogfood.toml:")
            for slot, header in headers.items():
                if not slot.startswith("_"):
                    self.stdout.write(f'  {slot:<12} = "{header}"')
            self.stdout.write("")
            self.stdout.write(f"[plumbline] UI logins (email or username, password: {SEED_PASSWORD}):")
            for label, email in (("admin", ADMIN_EMAIL), ("organizer", ORGANIZER_EMAIL)):
                if User.objects.filter(email__iexact=email).exists():
                    self.stdout.write(f"  {label:<12} {email}")
            self.stdout.write(f"  judge_a      {headers['_judge_a_email']}")
            self.stdout.write(f"  judge_b      {headers['_judge_b_email']}")
            self.stdout.write(f"  participant  {headers['_participant_email']}")

    # ------------------------------------------------------------------
    def load(self, data: dict) -> dict:
        ev = data["event"]
        close = parse_datetime(ev["submissions_close"])
        if close is None:
            raise CommandError("event.submissions_close is not ISO 8601")
        if timezone.is_naive(close):
            close = timezone.make_aware(close, UTC)

        event = Event.objects.filter(external_id=ev["id"]).order_by("id").first()
        if event is not None:
            # Loaded before. From here on the data belongs to the people using
            # the portal: nothing is written, not even a deleted account.
            return self.existing(event, data)
        created = True

        _user_for(ADMIN_EMAIL, "Portal Admin", is_staff=True, is_superuser=True)
        organizer = _user_for(ORGANIZER_EMAIL, "Sample Organizer", is_staff=True)
        event = Event.objects.create(
            external_id=ev["id"],
            slug=_free_slug(slugify(ev["name"]) or slugify(ev["id"])),
            name=ev["name"],
            tagline="Seeded from the DOGFOOD 2026 fixture set",
            submissions_open_at=close - timedelta(days=30),
            submissions_close_at=close,
            judging_open_at=close,
            created_by=organizer,
            reviews_per_project=3,
        )
        EventRole.objects.get_or_create(event=event, user=organizer, role=Role.ORGANIZER)

        tracks = {}
        for i, t in enumerate(data.get("tracks", [])):
            track, _ = Track.objects.get_or_create(
                event=event, external_id=t["id"], defaults={"name": t["name"], "order": i}
            )
            tracks[t["id"]] = track

        rubric, _ = Rubric.objects.get_or_create(event=event, defaults={"scale_min": 1, "scale_max": 5})
        criteria_keys = []
        for s in data.get("scores", []):
            for k in s.get("criteria", {}):
                if k not in criteria_keys:
                    criteria_keys.append(k)
        if not criteria_keys:
            criteria_keys = ["functionality", "quality", "innovation"]
        criteria = {}
        for i, key in enumerate(criteria_keys):
            c, _ = Criterion.objects.get_or_create(rubric=rubric, key=key, defaults={"name": key.title(), "order": i})
            criteria[key] = c

        judges = {}
        for j in data.get("judges", []):
            user = _user_for(j["email"], j.get("name", ""))
            role = EventRole.objects.filter(event=event, role=Role.JUDGE, external_id=j["id"]).first()
            if role is None:
                # First load only. Tracks an organizer has changed since are theirs to keep.
                role, fresh = EventRole.objects.get_or_create(
                    event=event, user=user, role=Role.JUDGE, defaults={"external_id": j["id"]}
                )
                if fresh or not role.external_id:
                    role.external_id = j["id"]
                    role.save(update_fields=["external_id"])
                    role.tracks.set([tracks[t] for t in j.get("tracks", []) if t in tracks])
            judges[j["id"]] = role.user

        teams = {}
        first_member_email = None
        for t in data.get("teams", []):
            team, team_is_new = Team.objects.get_or_create(
                event=event, external_id=t["id"], defaults={"name": t["name"]}
            )
            teams[t["id"]] = team
            for n, email in enumerate(t.get("members", [])):
                if first_member_email is None:
                    first_member_email = email
                if not team_is_new:
                    continue
                user = _user_for(email)
                TeamMembership.objects.get_or_create(
                    team=team,
                    user=user,
                    defaults={"role": TeamMembership.MemberRole.OWNER if n == 0 else TeamMembership.MemberRole.MEMBER},
                )
                EventRole.objects.get_or_create(event=event, user=user, role=Role.PARTICIPANT)

        projects = {}
        seen_titles: dict[tuple[int, str], Project] = {}
        for p in data.get("projects", []):
            team = teams[p["team"]]
            submitted_at = parse_datetime(p.get("submitted_at") or "") or close
            if timezone.is_naive(submitted_at):
                submitted_at = timezone.make_aware(submitted_at, UTC)
            project, _ = Project.objects.get_or_create(
                event=event,
                external_id=p["id"],
                defaults={
                    "team": team,
                    "track": tracks.get(p.get("track")),
                    "title": p.get("title", "Untitled"),
                    "tagline": p.get("summary", ""),
                    "repo_url": p.get("repo_url", ""),
                    "status": Project.Status.SUBMITTED,
                    "submitted_at": submitted_at,
                },
            )
            projects[p["id"]] = project
            key = (team.id, project.title.strip().lower())
            if key in seen_titles and project.duplicate_of_id is None and seen_titles[key].id != project.id:
                project.duplicate_of = seen_titles[key]
                project.save(update_fields=["duplicate_of"])
            else:
                seen_titles.setdefault(key, project)

        for s in data.get("scores", []):
            judge = judges.get(s["judge"])
            project = projects.get(s["project"])
            if judge is None or project is None:
                continue
            assignment, fresh = JudgeAssignment.objects.get_or_create(
                event=event, judge=judge, project=project, defaults={"batch": "fixture"}
            )
            if not fresh:
                continue  # already loaded; a score edited since then is not put back
            values = s.get("criteria") or {}
            for key, value in values.items():
                if key in criteria and value is not None:
                    Score.objects.update_or_create(
                        assignment=assignment, criterion=criteria[key], defaults={"value": int(value)}
                    )
            assignment.comment = s.get("comment") or ""
            if values:
                assignment.status = JudgeAssignment.Status.SUBMITTED
                assignment.submitted_at = assignment.submitted_at or project.submitted_at
            assignment.save()

        # Reproducible tokens for the checker.
        judge_ids = sorted(judges)
        if not judge_ids:
            raise CommandError("the fixture set has no judges")
        judge_a = judges[judge_ids[0]]
        judge_b = judges[judge_ids[1]] if len(judge_ids) > 1 else judge_a
        participant = _user_for(first_member_email) if first_member_email else organizer
        headers = self.headers(
            {"organizer": organizer, "judge_a": judge_a, "judge_b": judge_b, "participant": participant}, issue=True
        )

        record(
            "seed.fixtures",
            actor=None,
            event=event,
            detail={
                "created": created,
                "tracks": len(tracks),
                "judges": len(judges),
                "teams": len(teams),
                "projects": len(projects),
                "scores": len(data.get("scores", [])),
            },
            channel="seed",
        )
        return headers

    # ------------------------------------------------------------------
    PREFIXES = {"organizer": "org", "judge_a": "jdg_a", "judge_b": "jdg_b", "participant": "prt"}

    def headers(self, users: dict, issue: bool) -> dict:
        """The checker's auth headers. A token that was revoked stays revoked,
        and is reported as such rather than printed as if it worked."""
        out = {}
        for slot, prefix in self.PREFIXES.items():
            user = users.get(slot)
            raw = ApiToken.deterministic_raw(prefix)
            token = ApiToken.objects.filter(key_hash=ApiToken.hash_key(raw)).first()
            if token is None and issue and user is not None:
                token, _ = ApiToken.issue(user, label=f"seed:{slot}", raw=raw)
            if token is None:
                out[slot] = "(no seed token; issue one at /accounts/tokens/)"
            elif token.revoked_at is not None:
                out[slot] = "(revoked; issue a new token at /accounts/tokens/)"
            else:
                out[slot] = f"Authorization: Bearer {raw}"
        for slot in ("judge_a", "judge_b", "participant"):
            out[f"_{slot}_email"] = users[slot].email if users.get(slot) else "(account removed)"
        return out

    def existing(self, event: Event, data: dict) -> dict:
        """Headers for a fixture set that is already loaded. Reads only."""
        roles = {
            r.external_id: r.user
            for r in EventRole.objects.filter(event=event, role=Role.JUDGE)
            .exclude(external_id="")
            .select_related("user")
        }
        judge_ids = sorted(j["id"] for j in data.get("judges", []))
        first = next((m for t in data.get("teams", []) for m in t.get("members", [])), None)

        def by_email(email):
            return User.objects.filter(email__iexact=email).order_by("id").first() if email else None

        return self.headers(
            {
                "organizer": by_email(ORGANIZER_EMAIL),
                "judge_a": roles.get(judge_ids[0]) if judge_ids else None,
                "judge_b": roles.get(judge_ids[1] if len(judge_ids) > 1 else judge_ids[0]) if judge_ids else None,
                "participant": by_email(first),
            },
            issue=False,
        )
