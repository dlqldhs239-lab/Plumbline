"""Prizes, form questions, bulk import and the embeddable pages."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from events import extras, importer
from events import services as event_services
from events.models import CustomAnswer, CustomQuestion, Event, EventRole, Prize, Project, Role, Team
from judging import services as judging

from .base import SeededTestCase


class Fresh(SeededTestCase):
    def setUp(self):
        now = timezone.now()
        self.ev = Event.objects.create(
            slug="fresh",
            name="Fresh",
            submissions_open_at=now - timedelta(days=1),
            submissions_close_at=now + timedelta(days=1),
        )
        EventRole.objects.create(event=self.ev, user=self.organizer, role=Role.ORGANIZER)
        self.tools = self.ev.tracks.create(name="Developer tools")
        self.sec = self.ev.tracks.create(name="Security")
        self.client.force_login(self.organizer)
        self.base = f"/events/{self.ev.slug}/organize/"


class PrizeTests(Fresh):
    def test_add_edit_remove_in_the_console(self):
        page = self.base + "prizes/"
        self.assertContains(self.client.get(page), "No prizes yet")
        r = self.client.post(
            page, {"name": "First place", "amount": "800", "currency": "usd", "track": ""}, follow=True
        )
        self.assertContains(r, "First place saved")
        prize = Prize.objects.get(event=self.ev)
        self.assertEqual((str(prize.amount), prize.currency, prize.track), ("800.00", "USD", None))
        r = self.client.post(
            page, {"prize": prize.pk, "name": "Winner", "amount": "", "track": self.sec.pk}, follow=True
        )
        prize.refresh_from_db()
        self.assertEqual((prize.name, prize.amount, prize.track), ("Winner", None, self.sec))
        self.assertContains(self.client.get(f"/events/{self.ev.slug}/"), "Winner")
        self.client.post(page, {"action": "delete", "prize": prize.pk})
        self.assertEqual(Prize.objects.filter(event=self.ev).count(), 0)
        actions = list(self.ev.audit_entries.values_list("action", flat=True))
        for wanted in ("prize.create", "prize.update", "prize.delete"):
            self.assertIn(wanted, actions)

    def test_bad_input_is_refused_and_what_was_typed_stays(self):
        page = self.base + "prizes/"
        for data in (
            {"name": ""},
            {"name": "x" * 200},
            {"name": "P", "amount": "lots"},
            {"name": "P", "amount": "-5"},
            {"name": "P", "amount": "1e30"},
            {"name": "P", "currency": "DOLLARS"},
            {"name": "P", "currency": "U$"},
            {"name": "P", "track": self.event.tracks.first().pk},
            {"name": "P", "track": "abc"},
            {"name": "P", "order": "-1"},
        ):
            r = self.client.post(page, data)
            self.assertEqual(r.status_code, 200, data)
        self.assertEqual(Prize.objects.filter(event=self.ev).count(), 0)
        r = self.client.post(page, {"name": "Kept", "amount": "lots"})
        self.assertContains(r, 'value="Kept"')
        for bad in ("abc", "9" * 30):
            self.assertEqual(self.client.get(page + f"?edit={bad}").status_code, 404)
            self.assertEqual(self.client.post(page, {"action": "delete", "prize": bad}).status_code, 404)

    def test_only_organizers_and_only_their_event(self):
        for user in (self.judge_a, self.participant):
            self.client.force_login(user)
            for tail in ("prizes/", "questions/", "import/"):
                self.assertEqual(self.client.get(self.base + tail).status_code, 403, tail)
            with self.assertRaises(PermissionDenied):
                extras.save_prize(self.ev, user, {"name": "P"})
        other = Prize.objects.create(event=self.event, name="Elsewhere")
        with self.assertRaises(ValidationError):
            extras.save_prize(self.ev, self.organizer, {"name": "P"}, other)


class QuestionTests(Fresh):
    def test_a_question_reaches_the_form_and_the_project_page(self):
        page = self.base + "questions/"
        r = self.client.post(
            page,
            {
                "prompt": "Licence",
                "kind": "choice",
                "choices": "MIT\nApache 2.0\nMIT\n",
                "required": "1",
                "help_text": "<b>pick</b>",
            },
            follow=True,
        )
        self.assertContains(r, "Question saved")
        q = CustomQuestion.objects.get(event=self.ev)
        self.assertEqual((q.choices, q.required), (["MIT", "Apache 2.0"], True))
        alice = User.objects.create_user("alice", "alice@example.org", "pw")
        team = event_services.create_team(self.ev, alice, "Nightshift")
        self.client.force_login(alice)
        form = self.client.get(f"/events/{self.ev.slug}/projects/new/")
        self.assertContains(form, "Licence")
        self.assertContains(form, "&lt;b&gt;pick&lt;/b&gt;")
        r = self.client.post(
            f"/events/{self.ev.slug}/projects/new/", {"title": "T", "track": self.tools.pk, "save": "1"}
        )
        self.assertEqual(r.status_code, 200)  # required and unanswered
        r = self.client.post(
            f"/events/{self.ev.slug}/projects/new/",
            {"title": "T", "track": self.tools.pk, f"q_{q.pk}": "MIT", "save": "1"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CustomAnswer.objects.get(question=q).value, "MIT")
        self.assertIsNotNone(team)

    def test_rules(self):
        save = lambda **d: extras.save_question(self.ev, self.organizer, d)  # noqa: E731
        for data in (
            {"prompt": ""},
            {"prompt": "x" * 400},
            {"prompt": "Q", "kind": "nonsense"},
            {"prompt": "Q", "kind": "choice", "choices": "only one"},
            {"prompt": "Q", "kind": "choice", "choices": "\n".join(str(i) for i in range(40))},
        ):
            with self.assertRaises(ValidationError, msg=data):
                save(**data)
        q = save(prompt="Anything else?", kind="text", choices="ignored\nfor text")
        self.assertEqual(q.choices, [])
        box = save(prompt="Agree", kind="checkbox", required=True)
        self.assertFalse(box.required)  # a checkbox that must be ticked is not a question

    def test_kind_is_fixed_once_answered_and_removal_says_what_goes(self):
        q = extras.save_question(self.ev, self.organizer, {"prompt": "Why", "kind": "text"})
        team = Team.objects.create(event=self.ev, name="T")
        project = Project.objects.create(event=self.ev, team=team, title="P")
        CustomAnswer.objects.create(project=project, question=q, value="because")
        with self.assertRaises(ValidationError):
            extras.save_question(self.ev, self.organizer, {"prompt": "Why", "kind": "url"}, q)
        extras.save_question(self.ev, self.organizer, {"prompt": "Why, in a line", "kind": "text"}, q)
        page = self.client.get(self.base + "questions/")
        self.assertContains(page, "Remove with 1 answer")
        self.client.post(self.base + "questions/", {"action": "delete", "question": q.pk})
        entry = self.ev.audit_entries.get(action="question.delete")
        self.assertEqual(entry.detail["answers_removed"], 1)


PROJECTS = """team,title,tagline,track,repo_url,tags,members
Nightshift,Quiet Hours,Mutes the pager,Developer tools,https://example.org/r,django; postgres,ana@example.org; ben@example.org
Daybreak,First Light,,security,,,cy@example.org
Nightshift,Second Shift,,Developer tools,,,ana@example.org
"""


class ImportTests(Fresh):
    def test_plan_writes_nothing_and_apply_writes_everything(self):
        users = User.objects.count()
        plan = importer.plan(self.ev, self.organizer, PROJECTS, "projects")
        self.assertEqual((plan["total"], plan["bad"], plan["ready"]), (3, 0, True))
        self.assertEqual([r["line"] for r in plan["rows"]], [2, 3, 4])
        self.assertEqual((Project.objects.filter(event=self.ev).count(), User.objects.count()), (0, users))
        made = importer.apply(self.ev, self.organizer, PROJECTS, "projects")
        self.assertEqual(made, {"projects": 3, "teams": 2, "accounts": 3, "judges": 0})
        first = Project.objects.get(event=self.ev, title="Quiet Hours")
        self.assertEqual(
            (first.status, first.track, first.tech_tags), ("submitted", self.tools, ["django", "postgres"])
        )
        self.assertEqual(Project.objects.get(title="First Light").track, self.sec)  # track names ignore case
        night = Team.objects.get(event=self.ev, name="Nightshift")
        self.assertEqual(night.memberships.count(), 2)
        self.assertEqual(night.memberships.filter(role="owner").count(), 1)
        self.assertFalse(User.objects.get(email="ana@example.org").has_usable_password())
        self.assertContains(self.client.get(f"/events/{self.ev.slug}/gallery/"), "Quiet Hours")
        self.assertTrue(self.ev.audit_entries.filter(action="import.projects").exists())

    def test_one_bad_row_stops_everything(self):
        bad = PROJECTS + "Dusk,Broken,,No such track,javascript:alert(1),,not-an-email\n"
        plan = importer.plan(self.ev, self.organizer, bad, "projects")
        self.assertEqual((plan["bad"], plan["ready"]), (1, False))
        problems = " | ".join(plan["rows"][-1]["problems"])
        for wanted in ("no track called 'No such track'", "not an http or https address", "is not an email address"):
            self.assertIn(wanted, problems)
        with self.assertRaisesMessage(ValidationError, "nothing was imported"):
            importer.apply(self.ev, self.organizer, bad, "projects")
        self.assertEqual(Project.objects.filter(event=self.ev).count(), 0)
        self.assertEqual(Team.objects.filter(event=self.ev).count(), 0)
        self.assertFalse(User.objects.filter(email="ana@example.org").exists())

    def test_what_a_spreadsheet_does_to_a_file(self):
        semi = "﻿Team;Title;Members;Colour\r\nNightshift;Quiet Hours;ana@example.org;blue\r\n\r\n"
        plan = importer.plan(self.ev, self.organizer, semi, "projects")
        self.assertEqual((plan["total"], plan["ready"], plan["ignored_columns"]), (1, True, ["colour"]))

    def test_conflicts_inside_the_file_and_with_the_event(self):
        text = "team,title,members\nA,One,ana@example.org\nB,Two,ana@example.org\nA,One,\n"
        plan = importer.plan(self.ev, self.organizer, text, "projects")
        self.assertIn("already on team A", plan["rows"][1]["problems"][0])
        self.assertIn("earlier line", plan["rows"][2]["problems"][0])
        importer.apply(self.ev, self.organizer, "team,title\nA,One\n", "projects")
        again = importer.plan(self.ev, self.organizer, "team,title\na,one\n", "projects")
        self.assertIn("already has a project with this title", again["rows"][0]["problems"][0])

    def test_files_that_are_not_files(self):
        for text in ("", "   ", "just some words", "title\nNo team column\n", "team,title\n" + "A,B\n" * 2001):
            try:
                plan = importer.plan(self.ev, self.organizer, text, "projects")
                self.assertFalse(plan["ready"], text[:30])
            except ValidationError:
                pass
        with self.assertRaises(ValidationError):
            importer.plan(self.ev, self.organizer, "team,title\nA,B\n", "spells")
        with self.assertRaises(ValidationError):
            importer.plan(self.ev, self.organizer, "team,title\n" + "A" * 2_100_000, "projects")

    def test_judges(self):
        text = "email,name,tracks\njo@example.org,Jo Judge,Security; developer tools\nsam@example.org,,\n"
        made = importer.apply(self.ev, self.organizer, text, "judges")
        self.assertEqual(made["judges"], 2)
        jo = EventRole.objects.get(event=self.ev, role=Role.JUDGE, user__email="jo@example.org")
        self.assertEqual(set(jo.tracks.all()), {self.tools, self.sec})
        self.assertEqual(jo.user.get_full_name(), "Jo Judge")
        plan = importer.plan(self.ev, self.organizer, "email,tracks\njo@example.org,Nope\nx\n", "judges")
        self.assertEqual(plan["bad"], 2)
        self.assertIn("already a judge here", plan["rows"][0]["notes"][0])

    def test_console_flow(self):
        page = self.base + "import/"
        self.assertContains(self.client.get(page), "Check the file")
        r = self.client.get(page + "?kind=projects&template=1")
        self.assertTrue(r.content.decode().startswith("team,title"))
        r = self.client.post(page, {"kind": "projects", "text": PROJECTS})
        self.assertContains(r, "Ready: 3 rows")
        self.assertEqual(Project.objects.filter(event=self.ev).count(), 0)
        r = self.client.post(page, {"kind": "projects", "text": PROJECTS, "action": "apply"}, follow=True)
        self.assertContains(r, "3 projects imported")
        r = self.client.post(page, {"kind": "projects", "text": PROJECTS, "action": "apply"}, follow=True)
        self.assertContains(r, "nothing was imported")
        self.assertEqual(Project.objects.filter(event=self.ev).count(), 3)

    def test_api_is_a_dry_run_unless_told(self):
        url = f"/api/events/{self.ev.slug}/import"
        r = self.client.post(url, data={"csv": PROJECTS}, content_type="application/json", **self.bearer("organizer"))
        self.assertEqual((r.status_code, r.json()["dry_run"], r.json()["ready"]), (200, True, True))
        self.assertEqual(Project.objects.filter(event=self.ev).count(), 0)
        r = self.client.post(
            url, data={"csv": PROJECTS, "dry_run": False}, content_type="application/json", **self.bearer("organizer")
        )
        self.assertEqual(r.json()["imported"]["projects"], 3)
        r = self.client.post(url, data={"csv": PROJECTS}, content_type="application/json", **self.bearer("judge_a"))
        self.assertEqual(r.status_code, 403)


class ApiExtrasTests(Fresh):
    def call(self, method, tail, who="organizer", data=None):
        kwargs = self.bearer(who)
        if data is not None:
            kwargs.update(data=data, content_type="application/json")
        return getattr(self.client, method)(f"/api/events/{self.ev.slug}{tail}", **kwargs)

    def test_prizes_and_questions(self):
        r = self.call("post", "/prizes", data={"name": "First", "amount": 800, "track": self.sec.pk})
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()["id"]
        self.assertEqual(r.json()["track"], "Security")
        self.assertEqual(
            self.call("put", f"/prizes/{pid}", data={"name": "Top", "amount": None}).json()["amount"], None
        )
        self.assertEqual(self.call("post", "/prizes", who="participant", data={"name": "X"}).status_code, 403)
        self.assertEqual(self.call("post", "/prizes", data={"name": "X", "amount": -1}).status_code, 400)
        self.assertEqual(len(self.client.get(f"/api/events/{self.ev.slug}/prizes").json()), 1)
        self.assertEqual(self.call("delete", f"/prizes/{pid}", who="judge_a").status_code, 403)
        self.assertEqual(self.call("delete", f"/prizes/{pid}").status_code, 204)
        r = self.call("post", "/questions", data={"prompt": "Licence", "kind": "choice", "choices": ["MIT", "GPL"]})
        self.assertEqual(r.status_code, 201, r.content)
        qid = r.json()["id"]
        self.assertEqual(self.call("post", "/questions", data={"prompt": "Q", "kind": "choice"}).status_code, 400)
        self.assertEqual(
            self.call("put", f"/questions/{qid}", who="participant", data={"prompt": "Q"}).status_code, 403
        )
        self.assertEqual(self.client.get(f"/api/events/{self.ev.slug}/questions").json()[0]["choices"], ["MIT", "GPL"])
        self.assertEqual(self.call("delete", f"/questions/{qid}").status_code, 204)


class EmbedTests(SeededTestCase):
    def test_gallery_can_be_framed_and_nothing_else_can(self):
        r = self.client.get(f"/events/{self.event.slug}/embed/gallery/?limit=5")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("X-Frame-Options", r.headers)
        self.assertEqual(r.content.decode().count('class="project"'), 5)
        self.assertNotContains(r, "Sign in")
        self.assertEqual(self.client.get(f"/events/{self.event.slug}/gallery/")["X-Frame-Options"], "DENY")
        self.assertEqual(self.client.get("/accounts/login/")["X-Frame-Options"], "DENY")

    def test_results_only_once_published(self):
        url = f"/events/{self.event.slug}/embed/results/"
        judging.recompute_results(self.event, self.organizer)
        r = self.client.get(url)
        self.assertContains(r, "Not published yet")
        self.assertNotContains(r, "Iron Switch")
        judging.publish_results(self.event, self.organizer)
        r = self.client.get(url + "?limit=3")
        self.assertContains(r, "Iron Switch")
        self.assertEqual(r.content.decode().count("<tr>"), 4)

    def test_hidden_projects_and_odd_addresses(self):
        p = Project.objects.get(external_id="prj_01")
        event_services.set_hidden(p, self.organizer, True)
        self.assertNotContains(self.client.get(f"/events/{self.event.slug}/embed/gallery/?limit=60"), p.title)
        base = f"/events/{self.event.slug}/embed/"
        self.assertEqual(self.client.get(base + "audit/").status_code, 403)
        for q in ("?limit=abc", "?limit=-4", "?limit=999999999999999999999"):
            self.assertEqual(self.client.get(base + "gallery/" + q).status_code, 200, q)
        for q in ("?track=abc", "?track=999999"):
            self.assertEqual(self.client.get(base + "gallery/" + q).status_code, 404, q)
        track = self.event.tracks.get(external_id="trk_04")
        r = self.client.get(base + f"gallery/?track={track.pk}&limit=60")
        self.assertEqual(r.content.decode().count('class="project"'), 4)  # prj_01 of this track is hidden

    def test_the_console_gives_the_snippet(self):
        self.client.force_login(self.organizer)
        r = self.client.get(f"/events/{self.event.slug}/organize/integrations/")
        self.assertContains(r, f"/events/{self.event.slug}/embed/gallery/")
        self.assertContains(r, "&lt;iframe")
