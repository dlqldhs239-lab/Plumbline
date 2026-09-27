"""Bugs found by the third independent review, on 2026-09-27, in the records,
the import, the form questions and the exports."""

import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from accounts.models import ApiToken
from events import extras, importer
from events import services as event_services
from events.models import CustomAnswer, Event, EventRole, Project, Role, Team, TeamMembership
from judging import export
from judging import services as judging
from judging.models import ProjectResult
from records import services as records
from records.models import Record

from .base import SeededTestCase


class Issued(SeededTestCase):
    def setUp(self):
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        records.issue_for_event(self.event, self.organizer, places=3)
        self.first = ProjectResult.objects.get(event=self.event, rank=1)
        self.rec = Record.objects.filter(project=self.first.project, kind="placement").first()
        self.doc = records.document(self.rec)


class RecordsFollowTheResultsTests(Issued):
    def test_a_hidden_project_holds_no_standing_record(self):
        event_services.set_hidden(self.first.project, self.organizer, True)
        # At once, before anybody recomputes or issues again:
        self.assertEqual(records.check(self.doc["payload"], self.doc["signature"])["state"], "suspended")
        page = self.client.get(self.rec.get_absolute_url())
        self.assertContains(page, "Not standing at present")
        self.assertNotContains(page, self.first.project.title)
        self.assertNotContains(page, self.rec.payload["recipient"])
        self.assertEqual(self.client.get(f"/records/{self.rec.serial}.json").status_code, 403)
        api = self.client.get(f"/api/records/{self.rec.serial}").json()
        self.assertEqual(
            (api["state"], api["signature"], api["payload"]), ("suspended", "", {"serial": self.rec.serial})
        )
        # And after issuing again there is one first place, not two.
        judging.recompute_results(self.event, self.organizer)
        out = records.issue_for_event(self.event, self.organizer, places=3)
        self.assertGreater(out["withdrawn"], 0)
        standing_first = Record.objects.filter(event=self.event, revoked_at__isnull=True, payload__place=1)
        self.assertEqual({r.project_id for r in standing_first}, {judging.placed(self.event)[0].project_id})
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.revoke_reason, "no longer in the published results")

    def test_a_withdrawn_member_and_a_withdrawn_project(self):
        member = TeamMembership.objects.filter(team=self.first.project.team).first()
        theirs = Record.objects.get(project=self.first.project, recipient=member.user)
        member.delete()
        records.issue_for_event(self.event, self.organizer, places=3)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_revoked)
        other = ProjectResult.objects.get(event=self.event, rank=2).project
        Project.objects.filter(pk=other.pk).update(status=Project.Status.WITHDRAWN)
        rec = Record.objects.filter(project=other, revoked_at__isnull=True).first()
        self.assertEqual(records.state_of(rec)["state"], "suspended")

    def test_unpublishing_suspends_places_and_leaves_judging(self):
        judging.publish_results(self.event, self.organizer, False)
        self.assertEqual(records.check(self.doc["payload"], self.doc["signature"])["state"], "suspended")
        self.assertNotContains(self.client.get(self.rec.get_absolute_url()), "placed")
        judge = Record.objects.filter(kind="judge").first()
        self.assertEqual(records.state_of(judge)["state"], "genuine")
        judging.publish_results(self.event, self.organizer)
        self.assertEqual(records.state_of(self.rec)["state"], "genuine")


class CheckIsRobustTests(Issued):
    def test_what_used_to_crash_is_unknown_or_altered(self):
        deep = "x"
        for _ in range(2000):
            deep = [deep]
        cases = (
            (self.doc["payload"], "é" * 64),
            (self.doc["payload"], "\ud800"),
            ({"serial": self.rec.serial, "x": "\ud800"}, "00"),
            ({"serial": self.rec.serial, "x": deep}, "00"),
            ({"serial": ["a"]}, "00"),
            ({"serial": {"a": 1}}, "00"),
        )
        for payload, signature in cases:
            self.assertIn(records.check(payload, signature)["state"], ("unknown", "altered"))
        text = '{"payload":' + "[" * 9000 + "]" * 9000 + ',"signature":"x"}'
        self.assertEqual(self.client.post("/verify/", {"document": text}).status_code, 200)
        r = self.client.post(
            "/api/records/check",
            data={"payload": self.doc["payload"], "signature": "é" * 64},
            content_type="application/json",
        )
        self.assertEqual((r.status_code, r.json()["state"]), (200, "altered"))

    def test_a_key_written_twice_is_refused(self):
        text = json.dumps(self.doc)
        shown = text.replace('"place": ', '"place": 1, "place": ', 1)
        self.assertNotEqual(shown, text)
        r = self.client.post("/verify/", {"document": shown})
        self.assertContains(r, "Not found")
        self.assertNotContains(r, "Genuine")

    def test_revoke_takes_the_number_in_any_case_and_places_must_be_a_number(self):
        self.client.force_login(self.organizer)
        r = self.client.post(
            f"/events/{self.event.slug}/organize/records/{self.rec.serial.lower()}/revoke/", {"reason": "typed small"}
        )
        self.assertEqual(r.status_code, 302)
        self.rec.refresh_from_db()
        self.assertTrue(self.rec.is_revoked)
        for bad in (True, "many", -1, 101, 2.5):
            with self.assertRaises(ValidationError, msg=bad):
                records.issue_for_event(self.event, self.organizer, places=bad)

    @override_settings(PLUMBLINE_SITE_URL="https://judging.example.org")
    def test_a_forged_host_does_not_reach_the_certificate(self):
        page = self.client.get(self.rec.get_absolute_url(), HTTP_HOST="evil.example")
        self.assertContains(page, f"https://judging.example.org/records/{self.rec.serial}/")
        self.assertNotContains(page, "evil.example")
        api = self.client.get(f"/api/records/{self.rec.serial}", HTTP_HOST="evil.example").json()
        self.assertTrue(api["url"].startswith("https://judging.example.org/"))
        embed = self.client.get(f"/events/{self.event.slug}/embed/gallery/", HTTP_HOST="evil.example")
        self.assertNotContains(embed, "evil.example")


class FormulaTests(SeededTestCase):
    def test_cells_that_would_run_are_made_text(self):
        self.assertEqual(export.safe_cell("=1+1"), "'=1+1")
        self.assertEqual(export.safe_cell("@x"), "'@x")
        self.assertEqual(export.safe_cell("+cmd|' /C calc'!A0"), "'+cmd|' /C calc'!A0")
        self.assertEqual(export.safe_cell("\t=1"), "'\t=1")
        for plain in ("-3.5", "+2", "12", "Quiet Hours", "", 4, -2.5, None, True):
            self.assertEqual(export.safe_cell(plain), plain)
        self.assertTrue(export.safe_cell({"note": "=1"}).startswith('{"note"'))

    def test_every_export_is_covered(self):
        project = Project.objects.get(external_id="prj_01")
        Project.objects.filter(pk=project.pk).update(title='=HYPERLINK("http://evil.example","click")', tagline="@x")
        Team.objects.filter(pk=project.team_id).update(name="=1+1")
        a = project.assignments.first()
        a.comment = "-2+3"
        a.save()
        self.client.force_login(self.organizer)
        for kind in ("projects", "assignments", "scores", "results", "audit"):
            if kind == "results":
                judging.recompute_results(self.event, self.organizer)
            text = self.client.get(f"/events/{self.event.slug}/organize/export/{kind}.csv").content.decode()
            for line in text.splitlines()[1:]:
                for cell in next(__import__("csv").reader([line])):
                    self.assertFalse(cell.startswith(("=", "@")), (kind, cell[:40]))
        scores = self.client.get(f"/events/{self.event.slug}/organize/export/scores.csv").content.decode()
        self.assertIn("'-2+3", scores)


class RequiredQuestionTests(SeededTestCase):
    def setUp(self):
        now = timezone.now()
        self.ev = Event.objects.create(
            slug="asks",
            name="Asks",
            submissions_open_at=now - timedelta(days=1),
            submissions_close_at=now + timedelta(days=1),
        )
        EventRole.objects.create(event=self.ev, user=self.organizer, role=Role.ORGANIZER)
        self.q = extras.save_question(
            self.ev, self.organizer, {"prompt": "Licence", "kind": "choice", "choices": "MIT\nGPL", "required": True}
        )
        self.alice = User.objects.create_user("alice", "alice@example.org", "pw")
        event_services.create_team(self.ev, self.alice, "Nightshift")
        _, self.token = ApiToken.issue(self.alice)

    def post(self, body, tail=""):
        return self.client.post(
            f"/api/events/{self.ev.slug}/projects{tail}",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

    def test_the_api_cannot_submit_past_a_required_question(self):
        r = self.post({"title": "No answers", "submit": True})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Licence", r.json()["detail"])
        self.assertEqual(Project.objects.filter(event=self.ev).count(), 0)
        r = self.post({"title": "Draft is fine"})
        self.assertEqual(r.status_code, 201)
        pid = r.json()["id"]
        self.assertEqual(self.post(None, f"/{pid}/submit").status_code, 400)
        for bad in ({str(self.q.pk): "BSD"}, {"999999": "MIT"}, {"abc": "MIT"}, {str(self.q.pk): "x" * 6000}):
            r = self.client.patch(
                f"/api/events/{self.ev.slug}/projects/{pid}",
                data={"title": "Draft is fine", "answers": bad},
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )
            self.assertEqual(r.status_code, 400, bad)
        r = self.client.patch(
            f"/api/events/{self.ev.slug}/projects/{pid}",
            data={"title": "Draft is fine", "answers": {str(self.q.pk): "MIT"}, "submit": True},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(CustomAnswer.objects.get(question=self.q).value, "MIT")

    def test_choices_that_were_chosen_stay(self):
        project = event_services.create_project(
            self.ev, self.alice.team_memberships.first().team, self.alice, {"title": "P"}
        )
        event_services.set_answers(project, self.alice, {self.q.pk: "MIT"})
        with self.assertRaisesMessage(ValidationError, "Teams have chosen MIT"):
            extras.save_question(
                self.ev, self.organizer, {"prompt": "Licence", "kind": "choice", "choices": "GPL\nBSD"}, self.q
            )
        extras.save_question(
            self.ev, self.organizer, {"prompt": "Licence", "kind": "choice", "choices": "MIT\nGPL\nBSD"}, self.q
        )
        with self.assertRaises(ValidationError):
            extras.save_question(self.ev, self.organizer, {"prompt": "Q", "kind": "choice", "choices": ["a\nb", "c"]})

    def test_an_optional_choice_can_be_left_unanswered(self):
        extras.save_question(self.ev, self.organizer, {"prompt": "Size", "kind": "choice", "choices": "S\nL"})
        self.client.force_login(self.alice)
        page = self.client.get(f"/events/{self.ev.slug}/projects/new/")
        self.assertContains(page, "No answer")


class ImportReviewTests(SeededTestCase):
    def setUp(self):
        now = timezone.now()
        self.ev = Event.objects.create(
            slug="imp",
            name="Imp",
            submissions_open_at=now - timedelta(days=1),
            submissions_close_at=now + timedelta(days=1),
        )
        EventRole.objects.create(event=self.ev, user=self.organizer, role=Role.ORGANIZER)

    def plan(self, text, kind="projects", event=None):
        return importer.plan(event or self.ev, self.organizer, text, kind)

    def test_a_header_that_repeats_or_skips_a_name(self):
        with self.assertRaisesMessage(ValidationError, "names a column twice: tags"):
            self.plan("team,title,tags,tags,members\nT1,P1,a,b,ana@example.org\n")
        with self.assertRaisesMessage(ValidationError, "twice: team"):
            self.plan("team,team,title\nA,B,P1\n")
        out = self.plan("team,title,,,members\nT1,P1,x,y,ana@example.org\n")
        self.assertEqual(out["rows"][0]["row"]["members"], "ana@example.org")
        self.assertIn("a column without a name", out["ignored_columns"])
        with self.assertRaisesMessage(ValidationError, "more cells than"):
            self.plan("team,title\nT1,P1,stray\n")

    def test_an_oversized_header_cell_is_refused_not_crashed(self):
        for text in ('team,title,"' + "x" * 140_000, "team,title\nA," + "y" * 30_000 + "\n"):
            with self.assertRaises(ValidationError):
                self.plan(text)
        self.client.force_login(self.organizer)
        r = self.client.post(
            f"/events/{self.ev.slug}/organize/import/", {"kind": "projects", "text": 'team,title,"' + "x" * 140_000}
        )
        self.assertEqual(r.status_code, 200)

    def test_two_teams_of_one_name_are_not_guessed_between(self):
        a = Team.objects.create(event=self.ev, name="Twin")
        Team.objects.create(event=self.ev, name="Twin")
        twin = User.objects.create_user("twin", "twin@example.org", "pw")
        TeamMembership.objects.create(team=a, user=twin)
        out = self.plan("team,title,members\nTwin,P1,twin@example.org\n")
        self.assertFalse(out["ready"])
        self.assertIn("2 teams in this event are called", out["rows"][0]["problems"][0])
        with self.assertRaises(ValidationError):
            importer.apply(self.ev, self.organizer, "team,title,members\nTwin,P1,twin@example.org\n", "projects")
        self.assertEqual(TeamMembership.objects.filter(user=twin).count(), 1)

    def test_judges_organizers_and_staff_are_not_put_on_teams(self):
        jj = User.objects.create_user("jj", "jj@example.org", "pw")
        EventRole.objects.create(event=self.ev, user=jj, role=Role.JUDGE)
        plain = User.objects.create_user("plain", "plain@example.org", "pw")
        out = self.plan(
            "team,title,members\nA,P1,jj@example.org;admin@example.org;organizer@example.org;plain@example.org;new@example.org\n"
        )
        problems = " | ".join(out["rows"][0]["problems"])
        self.assertIn("jj@example.org is a judge of this event", problems)
        self.assertIn("organizer@example.org is an organizer of this event", problems)
        self.assertIn("admin@example.org is a staff account", problems)
        self.assertNotIn("plain@example.org", problems)
        self.assertIn("existing account", " ".join(out["rows"][0]["notes"]))
        self.assertIsNotNone(plain)
        on_team = self.plan("email\nplain@example.org\n", "judges")
        self.assertTrue(on_team["ready"])
        team = Team.objects.create(event=self.ev, name="B")
        TeamMembership.objects.create(team=team, user=plain)
        self.assertIn("cannot judge it", self.plan("email\nplain@example.org\n", "judges")["rows"][0]["problems"][0])

    def test_what_passes_the_check_passes_the_import(self):
        long = "a" * 250 + "@example.org"
        for kind, text in (("projects", f"team,title,members\nA,P,{long}\n"), ("judges", f"email\n{long}\n")):
            out = self.plan(text, kind)
            self.assertFalse(out["ready"], kind)
            self.assertIn("longer than 254", out["rows"][0]["problems"][0])

    def test_the_organizer_is_told_what_kind_of_event_they_import_into(self):
        out = self.plan("team,title\nA,P\n", event=self.event)
        self.assertIn("Submissions have closed", out["warnings"][0])
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        out = self.plan("team,title\nA,P\n", event=self.event)
        self.assertIn("results of this event are published", out["warnings"][0])
        extras.save_question(self.ev, self.organizer, {"prompt": "Why", "required": True})
        self.assertIn("1 required question", self.plan("team,title\nA,P\n")["warnings"][0])

    def test_a_name_differing_only_in_case_is_said_to_join(self):
        Team.objects.create(event=self.ev, name="Nightshift")
        out = self.plan("team,title\nNIGHTSHIFT,P2\n")
        self.assertEqual(out["rows"][0]["notes"][0], "joins the existing team Nightshift")
