"""Seed "Open House": a second event in which every window is open.

The organizer's fixture event closed months ago and every review in it is
submitted. That is right for comparing portals and wrong for trying one:
there is nothing left to submit, score or vote on. Open House is the event
to try things in. It is ours, not the organizer's, and says so on its page.

Everything here goes through the same services the pages and the API use,
so the audit log of the event reads as if people had done it by hand.
Loaded once; never touched again.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from audit.services import record
from community.models import Comment, Vote, Voter
from community.services import _hash
from events import extras, services, theme
from events.models import Event, EventRole, Project, Role, TeamMembership
from judging import pairwise_services
from judging import services as judging
from judging.models import JudgeAssignment

from .seed_fixtures import ORGANIZER_EMAIL, _user_for

EXTERNAL_ID = "open_house"

TRACKS = ["Tools for organizers", "Tools for judges", "Tools for teams"]

CRITERIA = [
    {"key": "works", "name": "Does it work", "weight": 2, "description": "Started it, used it, nothing fell over."},
    {"key": "useful", "name": "Would we use it", "weight": 2, "description": "At the next event, not in principle."},
    {"key": "craft", "name": "Craft", "weight": 1, "description": "Code, documents and interface read as cared for."},
    {"key": "idea", "name": "The idea", "weight": 1, "description": "Something we had not seen before."},
]

# team, members, title, track, tagline, tags, licence, quality (what a fair judge would give, 1 to 5), description
PROJECTS = [
    (
        "Margin of Error",
        ["mina.okafor", "jun.alvarez"],
        "Second Reader",
        1,
        "Tells an organizer which reviews disagree enough to need a third judge.",
        ["python", "statistics"],
        "MIT",
        4.4,
        "Two judges give the same project a 2 and a 5. Most portals average that to 3.5 and move on.\n\n"
        "Second Reader looks at every project after the second review comes in, measures how far apart "
        "the two are against how far apart that pair of judges usually is, and puts the projects that "
        "need another pair of eyes at the top of the organizer's list.\n\n"
        "It reads a scores export and writes a list. No accounts, no database.",
    ),
    (
        "Platform Nine",
        ["sofia.lindgren", "arjun.pillai", "tess.moreau"],
        "Late Train",
        2,
        "A submission form that keeps your draft when the venue network drops.",
        ["javascript", "service-worker", "indexeddb"],
        "Apache-2.0",
        4.1,
        "At the last event we ran, the network went down forty minutes before the deadline and three "
        "teams lost what they had typed.\n\n"
        "Late Train keeps the whole form in the browser and sends it when the connection comes back. "
        "The server still decides whether the deadline was met, with its own clock. The page tells the "
        "team which of the two happened.",
    ),
    (
        "One Short",
        ["daniel.achebe"],
        "Quorum",
        0,
        "Finds the smallest panel that still gives every project three reviews.",
        ["go", "scheduling"],
        "MIT",
        3.6,
        "Given the projects, the tracks and who cannot judge what, Quorum answers one question: how "
        "many judges do we have to invite?\n\n"
        "It treats the assignment as a flow problem and prints the panel size, the load per judge and "
        "the projects that stay short if one judge drops out.",
    ),
    (
        "Red Pencil",
        ["hana.kobayashi", "luis.ferreira"],
        "Plain Rubric",
        1,
        "Rewrites rubric criteria until two judges read them the same way.",
        ["typescript", "nlp"],
        "MIT",
        3.2,
        '"Innovation" means something different to every judge. Plain Rubric takes a criterion, asks '
        "for one project that deserves a 5 and one that deserves a 2, and turns the pair into the "
        "sentence that goes on the scoring form.\n\n"
        "The rubric in this event was written with it.",
    ),
    (
        "Front Desk",
        ["amelie.durand", "kwame.boateng"],
        "Roll Call",
        0,
        "Check-in by QR code that works on the organizer's phone with no signal.",
        ["kotlin", "qr", "offline"],
        "GPL-3.0",
        3.9,
        "The attendee list is loaded onto the phone before the doors open. Each badge is a signed code, "
        "so the phone can tell a real one from a photograph of a stranger's without asking a server.\n\n"
        "When the phone is online again the list is sent back, with the time each person came in.",
    ),
    (
        "Carbon Copy",
        ["nora.haugen"],
        "Paper Trail",
        0,
        "Turns an audit log into the two paragraphs you send when a team disputes a result.",
        ["python", "sqlite"],
        "MIT",
        3.4,
        'Every organizer has written the email that starts "we have looked into your score".\n\n'
        "Paper Trail reads the audit export for one project and writes what happened to it, in order: "
        "who reviewed it, when, whether anything was changed afterwards and by whom. It names no judge.",
    ),
    (
        "Five Minute Call",
        ["ivo.marin", "chiara.bellini", "sam.oyelaran"],
        "Green Room",
        2,
        "A shared checklist for the last hour before a demo.",
        ["elixir", "liveview"],
        "MIT",
        2.8,
        "Is the repository public. Does the video play without signing in. Is the demo account's "
        "password the one in the README.\n\n"
        "Green Room is that list, shared between teammates, with the checks that can be automated "
        "already ticked.",
    ),
    (
        "Photo Finish",
        ["farah.siddiqui", "peter.novak"],
        "Tiebreak",
        1,
        "Shows judges the two projects they scored the same and asks which was better.",
        ["rust", "wasm"],
        "Apache-2.0",
        4.0,
        "A judge who gives two projects 3.75 has not said they are equal. They have run out of scale.\n\n"
        "Tiebreak collects the pairs a judge left tied and asks one question about each. The answers "
        "break the tie without touching the scores.",
    ),
]

# Fixture judges who take part here. The first two are the checker's judge_a
# and judge_b: their reviews are left open, so whoever signs in as them has
# something to score.
OPEN_FOR = ["tomas.varga", "wei.lindqvist"]
PANEL = [
    # local part, name, how far this judge marks from fair
    ("tomas.varga", "Tomas Varga", 0.0),
    ("wei.lindqvist", "Wei Lindqvist", 0.0),
    ("amara.silva", "Amara Silva", +0.6),
    ("bruno.costa", "Bruno Costa", -0.8),
    ("diego.herrera", "Diego Herrera", +0.2),
    ("felix.roth", "Felix Roth", -0.3),
    ("ines.rocha", "Ines Rocha", +0.9),
    ("hiro.tanaka", "Hiro Tanaka", -0.5),
]

FEEDBACK = [
    "Ran it on our last export. It found the two projects we argued about, and one we had missed.",
    "Works as described. The README assumes more than it should about the input format.",
    "I would use this on Monday. The second screen needs a way back.",
    "A good idea, and half of it is built. The half that is built is careful.",
    "Started on the first try. I could not make it fail, and I tried.",
    "Clear about what it does not do, which I appreciated more than another feature.",
]

COMMENTS = [
    (
        0,
        "sofia.lindgren",
        "We could have used this in March. Does it need both reviews to be submitted, or is a draft enough?",
    ),
    (0, "mina.okafor", "Submitted only. A draft changes too often to raise a flag on."),
    (
        1,
        "daniel.achebe",
        "Tried it on the train, fittingly. Lost the connection twice and the draft was there both times.",
    ),
    (4, "nora.haugen", "How long does a badge stay valid? Asking because our events run over two days."),
    (7, "hana.kobayashi", "This is the question I wish the scoring form had asked me."),
]


def _digit(text: str, modulo: int) -> int:
    """A number that is the same on every machine: the seed must not differ
    between the portal a judge starts and the one in the screenshots."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % modulo


def _mark(quality: float, lean: float, judge: str, project: str, criterion: str) -> int:
    wobble = (_digit(f"{judge}|{project}|{criterion}", 9) - 4) / 4  # -1 to +1
    return max(1, min(5, round(quality + lean + 0.7 * wobble)))


class Command(BaseCommand):
    help = "Seed the Open House event (once): an event with every window open, to try the portal in."

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        if Event.objects.filter(external_id=EXTERNAL_ID).exists():
            return
        organizer = User.objects.filter(email__iexact=ORGANIZER_EMAIL).order_by("id").first()
        if organizer is None:
            # The fixture set was not loaded, so there is nobody to run this event.
            return
        with transaction.atomic():
            event = self.load(organizer)
        if not options["quiet"]:
            self.stdout.write(f"[plumbline] Open House is at /events/{event.slug}/ : every window is open.")

    def load(self, organizer) -> Event:
        now = timezone.now()
        event = services.create_event(
            organizer,
            {
                "name": "Open House",
                "slug": "open-house",
                "tagline": "An event with every window open, to try the portal in.",
                "description": (
                    "This event is not part of the DOGFOOD fixture set. It comes with Plumbline so that "
                    "there is something to do on a fresh installation: submit a project, score one, "
                    "compare two, cast a vote, publish the results.\n\n"
                    "Submissions, judging and voting are all open at once. At a real event they would "
                    "follow one another."
                ),
                "submissions_open_at": now - timedelta(days=9),
                "submissions_close_at": now + timedelta(days=21),
                "judging_open_at": now - timedelta(days=2),
                "voting_access": Event.VotingAccess.OPEN,
                "voting_open_at": now - timedelta(days=2),
                "voting_close_at": now + timedelta(days=21),
                "reviews_per_project": 3,
                **theme.preset("amber"),
            },
            tracks=TRACKS,
        )
        Event.objects.filter(pk=event.pk).update(external_id=EXTERNAL_ID)
        event.refresh_from_db()
        tracks = list(event.tracks.order_by("order", "id"))

        judging.replace_criteria(judging.ensure_rubric(event), organizer, CRITERIA)
        for name, amount, track in (
            ("First place", 800, None),
            ("Second place", 400, None),
            ("Best tool for judges", 200, 1),
        ):
            extras.save_prize(
                event,
                organizer,
                {
                    "name": name,
                    "amount": amount,
                    "currency": "USD",
                    "track": tracks[track].pk if track is not None else "",
                },
            )
        licence = extras.save_question(
            event,
            organizer,
            {
                "prompt": "Licence",
                "kind": "choice",
                "choices": "MIT\nApache-2.0\nGPL-3.0",
                "required": True,
                "help_text": "The winning project is forked and run, so it has to be open source.",
            },
        )
        another_week = extras.save_question(
            event, organizer, {"prompt": "What would you do with another week?", "kind": "textarea", "required": False}
        )

        people, projects, quality = {}, [], {}
        for team_name, members, title, track, tagline, tags, chosen, fair, description in PROJECTS:
            users = [_user_for(f"{m}@example.org", m.replace(".", " ").title()) for m in members]
            for u in users:
                people[u.email.split("@")[0]] = u
            owner = users[0]
            team = services.create_team(event, owner, team_name)
            for u in users[1:]:
                TeamMembership.objects.get_or_create(team=team, user=u)
                EventRole.objects.get_or_create(event=event, user=u, role=Role.PARTICIPANT)
            project = services.create_project(
                event,
                team,
                owner,
                {
                    "title": title,
                    "tagline": tagline,
                    "description": description,
                    "track": tracks[track],
                    "repo_url": f"https://example.org/open-house/{title.lower().replace(' ', '-')}",
                    "tech_tags": tags,
                },
            )
            services.set_answers(
                project,
                owner,
                {licence.pk: chosen, another_week.pk: "Write the part of the README that explains how to undo it."},
            )
            services.submit_project(project, owner)
            projects.append(project)
            quality[project.pk] = fair
        # One team has started and not finished, so the organizer has a draft to see.
        draft_owner = _user_for("oskar.lind@example.org", "Oskar Lind")
        draft_team = services.create_team(event, draft_owner, "Standing Desk")
        services.create_project(
            event,
            draft_team,
            draft_owner,
            {"title": "Standing Desk", "tagline": "Not ready to say yet.", "track": tracks[2]},
        )

        lean, judges = {}, {}
        for local, name, how in PANEL:
            role = services.add_judge(event, organizer, f"{local}@example.org", name)
            judges[local], lean[role.user_id] = role.user, how
        judging.assign_balanced(event, organizer, 3, batch="first-round", seed=7)

        keys = [c["key"] for c in CRITERIA]
        done = 0
        for a in JudgeAssignment.objects.filter(event=event).select_related("judge", "project").order_by("id"):
            local = a.judge.email.split("@")[0]
            if local in OPEN_FOR:
                continue
            marks = {k: _mark(quality[a.project_id], lean[a.judge_id], local, a.project.title, k) for k in keys}
            text = FEEDBACK[_digit(f"{local}|{a.project.title}", len(FEEDBACK))] if done % 2 == 0 else ""
            judging.save_scores(a, a.judge, marks, comment=text, submit=True)
            done += 1
        # Wei has started one review and left it, as people do.
        started = JudgeAssignment.objects.filter(event=event, judge=judges["wei.lindqvist"]).order_by("id").first()
        if started is not None:
            judging.save_scores(started, started.judge, {"works": 4, "useful": 4}, comment=None, submit=False)

        pairwise_services.set_enabled(event, organizer, True)
        for local, judge in judges.items():
            if local in OPEN_FOR:
                continue
            while True:
                pair = pairwise_services.next_pair(judge, event)
                if pair is None:
                    break
                a, b = pair
                gap = (
                    quality[a.pk]
                    + 0.1 * _digit(f"{local}|{a.pk}", 5)
                    - quality[b.pk]
                    - 0.1 * _digit(f"{local}|{b.pk}", 5)
                )
                preferred = None if abs(gap) < 0.15 else (a.pk if gap > 0 else b.pk)
                pairwise_services.record_comparison(judge, event, a.pk, b.pk, preferred)

        for at, local, body in COMMENTS:
            Comment.objects.create(project=projects[at], author=people[local], body=body)

        self.ballots(event, projects, quality)
        # Computed, not published: publishing is the organizer's to try.
        judging.recompute_results(event, organizer)
        record(
            "seed.open_house",
            actor=None,
            event=event,
            detail={"projects": len(projects), "judges": len(judges), "reviews_submitted": done},
            channel="seed",
        )
        return event

    def ballots(self, event: Event, projects: list[Project], quality: dict):
        """Eighteen ballots from the open link. Four came from one address,
        which is what the integrity page is there to show."""
        liked = sorted(projects, key=lambda p: -quality[p.pk])
        for n in range(18):
            shared = n >= 14
            address = "203.0.113.77" if shared else f"198.51.100.{10 + n}"
            voter = Voter(
                event=event,
                kind=Voter.Kind.OPEN,
                ip_hash=_hash(address),
                ua_hash=_hash("seeded browser" if shared else f"browser {n}"),
                last_seen_at=timezone.now(),
            )
            twins = Voter.objects.filter(event=event, kind=Voter.Kind.OPEN, ip_hash=voter.ip_hash).count()
            flags = []
            if twins >= 1:
                flags.append("shared_ip")
            if twins >= 3:
                flags.append("many_ballots_same_ip")
            voter.flags = flags
            voter.save()
            if shared:
                chosen = [projects[6]]  # four ballots, one address, one project
            else:
                first = _digit(f"ballot {n}", 4)
                chosen = [liked[first], liked[(first + 1 + _digit(f"second {n}", 5)) % len(liked)]]
            for p in chosen:
                Vote.objects.get_or_create(event=event, voter=voter, project=p, defaults={"weight": 1})
