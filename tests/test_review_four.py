"""Bugs found by the fourth independent review, on 2026-09-27, in pairwise
judging, the form answers, the records and the exports."""

import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import OperationalError, connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from events import extras
from events import services as event_services
from events.models import CustomAnswer, Event, EventRole, Project, Role, TeamMembership
from judging import export
from judging import pairwise_services as pw
from judging import services as judging
from judging.models import JudgeAssignment, PairwiseComparison, ProjectResult
from plumbline.config import site_address
from plumbline.middleware import OutOfRangeMiddleware
from records import services as records
from records.models import Record

from .base import SeededTestCase


class Pairs(SeededTestCase):
    def setUp(self):
        pw.set_enabled(self.event, self.organizer, True)
        self.judge = self.judge_b
        self.page = f"/judge/{self.event.slug}/compare/"

    def answer(self, judge, times=1):
        for _ in range(times):
            a, b = pw.next_pair(judge, self.event)
            pw.record_comparison(judge, self.event, a.id, b.id, a.id)


class TheJudgeDoesNotChooseTests(Pairs):
    def test_no_more_than_was_asked_for(self):
        self.answer(self.judge, 9)
        done = pw.done_by(self.judge, self.event)
        mine = pw.pool(self.judge, self.event)
        left = [(a, b) for n, a in enumerate(mine) for b in mine[n + 1 :] if (a.id, b.id) not in done and a.id < b.id]
        self.assertEqual(len(left), 6)
        for a, b in left:
            with self.assertRaises(ValidationError):
                pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        self.assertEqual(PairwiseComparison.objects.filter(judge=self.judge).count(), 9)

    def test_only_the_pair_that_was_shown(self):
        shown = pw.next_pair(self.judge, self.event)
        other = next(p for p in pw.pool(self.judge, self.event) if p.id not in (shown[0].id, shown[1].id))
        with self.assertRaises(ValidationError):
            pw.record_comparison(self.judge, self.event, shown[0].id, other.id, other.id)
        self.client.force_login(self.judge)
        r = self.client.post(self.page, {"first": shown[0].id, "second": other.id, "preferred": other.id}, follow=True)
        self.assertContains(r, "not the pair you were shown")
        self.assertEqual(PairwiseComparison.objects.count(), 0)
        # Either way round is the same pair.
        pw.record_comparison(self.judge, self.event, shown[1].id, shown[0].id, shown[0].id)

    def test_what_other_judges_do_does_not_change_the_pair_on_the_page(self):
        shown = [p.id for p in pw.next_pair(self.judge, self.event)]
        for role in EventRole.objects.filter(event=self.event, role=Role.JUDGE).exclude(user=self.judge)[:12]:
            while pw.next_pair(role.user, self.event):
                self.answer(role.user)
        self.assertEqual([p.id for p in pw.next_pair(self.judge, self.event)], shown)

    def test_the_same_answer_sent_twice_at_once_is_refused_not_a_crash(self):
        a, b = pw.next_pair(self.judge, self.event)
        left, right = sorted((a.id, b.id))
        # The second request passed every check before the first one wrote.
        from unittest import mock

        real = PairwiseComparison.objects.filter

        def blind(*args, **kwargs):
            if "left_id" in kwargs:
                return PairwiseComparison.objects.none()
            return real(*args, **kwargs)

        PairwiseComparison.objects.create(event=self.event, judge=self.judge, left_id=left, right_id=right)
        with mock.patch.object(PairwiseComparison.objects, "filter", side_effect=blind):
            with mock.patch.object(pw, "next_pair", return_value=(a, b)):
                with self.assertRaises(ValidationError):
                    pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        self.assertEqual(PairwiseComparison.objects.count(), 1)


class OwnTeamTests(Pairs):
    def test_a_judge_who_joined_a_team_neither_compares_nor_scores_its_project(self):
        assignment = (
            JudgeAssignment.objects.filter(judge=self.judge, event=self.event)
            .exclude(status=JudgeAssignment.Status.SUBMITTED)
            .first()
        )
        if assignment is None:
            assignment = JudgeAssignment.objects.filter(judge=self.judge, event=self.event).first()
            assignment.status = JudgeAssignment.Status.PENDING
            assignment.save(update_fields=["status"])
        project = assignment.project
        TeamMembership.objects.create(team=project.team, user=self.judge)
        self.assertNotIn(project.id, {p.id for p in pw.pool(self.judge, self.event)})
        self.assertEqual(pw.status(self.judge, self.event)["projects"], 5)
        other = pw.pool(self.judge, self.event)[0]
        with self.assertRaises(PermissionDenied):
            pw.record_comparison(self.judge, self.event, project.id, other.id, project.id)
        with self.assertRaises(PermissionDenied):
            judging.save_scores(assignment, self.judge, {"functionality": 5})


class ComparisonsFollowTheAssignmentTests(Pairs):
    def test_unassigning_takes_the_comparisons_out(self):
        JudgeAssignment.objects.filter(judge=self.judge, event=self.event).update(status=JudgeAssignment.Status.PENDING)
        self.answer(self.judge, 3)
        self.assertEqual(len(pw.collect(self.event)), 3)
        row = PairwiseComparison.objects.filter(judge=self.judge).first()
        judging.unassign(JudgeAssignment.objects.get(judge=self.judge, project_id=row.left_id), self.organizer)
        self.assertEqual(len(pw.collect(self.event)), 2)
        self.assertEqual(PairwiseComparison.objects.count(), 3)  # kept on the record, left out of the count

    def test_removing_the_judge_takes_them_out(self):
        self.answer(self.judge, 3)
        self.answer(self.judge_a, 0)
        JudgeAssignment.objects.filter(judge=self.judge).delete()
        self.assertEqual(pw.collect(self.event), [])
        judging.recompute_results(self.event, self.organizer)
        summary = pw.summary(self.event, self.organizer)
        self.assertEqual((summary["judges"], summary["comparisons"], summary["rows"]), (0, 0, []))


class StaleAndShownTests(Pairs):
    def setUp(self):
        super().setUp()
        self.answer(self.judge, 9)
        judging.recompute_results(self.event, self.organizer)

    def test_one_gone_and_one_arrived_is_still_stale(self):
        self.assertFalse(pw.summary(self.event, self.organizer)["stale"])
        before = len(pw.collect(self.event))
        other = next(
            r.user
            for r in EventRole.objects.filter(event=self.event, role=Role.JUDGE).exclude(user=self.judge)
            if len(pw.pool(r.user, self.event)) >= 2
        )
        self.answer(other)
        mine = {p.id for p in pw.pool(self.judge, self.event)}
        added = PairwiseComparison.objects.filter(judge=other).first()
        victim = next(
            r.project
            for r in ProjectResult.objects.filter(event=self.event, project_id__in=mine).select_related("project")
            if r.project_id not in (added.left_id, added.right_id)
        )
        gone = PairwiseComparison.objects.filter(judge=self.judge, left_id=victim.id).count()
        gone += PairwiseComparison.objects.filter(judge=self.judge, right_id=victim.id).count()
        event_services.set_hidden(victim, self.organizer, True)
        self.assertEqual(len(pw.collect(self.event)), before + 1 - gone)
        summary = pw.summary(self.event, self.organizer)
        self.assertTrue(summary["stale"])
        self.assertNotIn(victim.id, {r.project_id for r in summary["rows"]})

    def test_places_are_counted_among_the_rows_shown(self):
        url = f"/api/events/{self.event.slug}/pairwise"
        first = ProjectResult.objects.filter(event=self.event, pairwise_rank=1).first()
        event_services.set_hidden(first.project, self.organizer, True)
        rows = self.client.get(url, **self.bearer("organizer")).json()
        self.assertEqual(rows[0]["rank"], 1)
        self.assertNotIn(first.project_id, {r["project_id"] for r in rows})
        places = {r.project_id: r.place for r in judging.placed(self.event)}
        for r in rows:
            self.assertEqual(r["rubric_rank"], places[r["project_id"]])

    def test_switched_off_there_is_nothing_to_show(self):
        pw.set_enabled(self.event, self.organizer, False)
        r = self.client.get(f"/api/events/{self.event.slug}/pairwise", **self.bearer("organizer"))
        self.assertEqual((r.status_code, r.json()), (200, []))


class Issued(SeededTestCase):
    def setUp(self):
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        records.issue_for_event(self.event, self.organizer, places=3)
        self.first = ProjectResult.objects.get(event=self.event, rank=1)
        self.rec = Record.objects.filter(project=self.first.project, kind="placement").first()

    def shows_nothing(self, rec):
        page = self.client.get(rec.get_absolute_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, rec.payload["recipient"])
        self.assertNotContains(page, rec.payload["project"])
        self.assertEqual(self.client.get(f"/records/{rec.serial}.json").status_code, 403)
        api = self.client.get(f"/api/records/{rec.serial}").json()
        self.assertEqual((api["payload"], api["signature"]), ({"serial": rec.serial}, ""))
        return api


class WithdrawnRecordsTests(Issued):
    def test_a_withdrawn_record_keeps_quiet_while_the_results_are_not_public(self):
        records.revoke(self.rec, self.organizer, "issued to the wrong person")
        page = self.client.get(self.rec.get_absolute_url())
        self.assertContains(page, self.rec.payload["recipient"])  # results are public: nothing to keep quiet
        judging.publish_results(self.event, self.organizer, False)
        api = self.shows_nothing(self.rec)
        self.assertEqual(api["state"], "revoked")

    def test_and_while_its_project_is_hidden(self):
        records.revoke(self.rec, self.organizer, "issued to the wrong person")
        event_services.set_hidden(self.first.project, self.organizer, True)
        self.shows_nothing(self.rec)

    def test_a_statement_an_organizer_withdrew_is_not_made_again(self):
        records.revoke(self.rec, self.organizer, "issued to the wrong person")
        out = records.issue_for_event(self.event, self.organizer, places=3)
        self.assertEqual((out["issued"], out["held_back"]), (0, 1))
        again = Record.objects.filter(project=self.first.project, recipient=self.rec.recipient, revoked_at__isnull=True)
        self.assertFalse(again.exists())
        self.client.force_login(self.organizer)
        r = self.client.post(
            f"/events/{self.event.slug}/organize/records/", {"action": "issue", "places": "3"}, follow=True
        )
        self.assertContains(r, "1 not issued again")

    def test_but_a_record_the_portal_replaced_is(self):
        Record.objects.filter(pk=self.rec.pk).update(revoked_at=timezone.now(), revoke_reason=records.REPLACED)
        out = records.issue_for_event(self.event, self.organizer, places=3)
        self.assertEqual((out["issued"], out["held_back"]), (1, 0))


class RecordsStateWhatTheResultsStateTests(Issued):
    def test_a_place_that_changed_does_not_stand(self):
        last = ProjectResult.objects.filter(event=self.event, rank__isnull=False).order_by("-rank").first()
        ProjectResult.objects.filter(pk=self.first.pk).update(rank=last.rank + 1)
        state = records.state_of(self.rec)
        self.assertEqual((state["state"], state["withheld"]), ("suspended", True))
        self.assertIn("changed since", state["says"])
        self.assertEqual(self.shows_nothing(self.rec)["state"], "suspended")
        r = self.client.post("/verify/", {"document": json.dumps(records.document(self.rec))})
        self.assertNotContains(r, "Genuine")
        ProjectResult.objects.filter(pk=self.first.pk).update(rank=1)
        self.assertEqual(records.state_of(self.rec)["state"], "genuine")

    def test_a_score_that_changed_does_not_stand(self):
        ProjectResult.objects.filter(pk=self.first.pk).update(adjusted_mean=self.first.adjusted_mean + 0.01)
        self.assertEqual(records.state_of(self.rec)["state"], "suspended")

    def test_someone_who_left_the_team_holds_nothing(self):
        for kind in ("placement", "participation"):
            rec = Record.objects.filter(kind=kind, revoked_at__isnull=True).first()
            self.assertEqual(records.state_of(rec)["state"], "genuine", kind)
            TeamMembership.objects.filter(team=rec.project.team, user=rec.recipient).delete()
            state = records.state_of(rec)
            self.assertEqual(state["state"], "suspended", kind)
            self.assertIn("not on that team", state["says"])

    def test_the_console_reads_the_results_once(self):
        self.client.force_login(self.organizer)
        ProjectResult.objects.filter(pk=self.first.pk).update(rank=50)
        with CaptureQueriesContext(connection) as seen:
            page = self.client.get(f"/events/{self.event.slug}/organize/records/")
        self.assertContains(page, "Not standing")
        # More than a hundred records; the standings are not read once for each.
        self.assertGreater(Record.objects.filter(event=self.event).count(), 100)
        self.assertLess(len(seen), 40)


class Open(SeededTestCase):
    """An event that takes submissions, with one required question of each kind."""

    def setUp(self):
        now = timezone.now()
        self.ev = Event.objects.create(
            slug="open",
            name="Open",
            submissions_open_at=now - timedelta(days=1),
            submissions_close_at=now + timedelta(days=1),
        )
        EventRole.objects.create(event=self.ev, user=self.organizer, role=Role.ORGANIZER)
        self.why = extras.save_question(self.ev, self.organizer, {"prompt": "Why", "kind": "text", "required": True})
        self.licence = extras.save_question(
            self.ev, self.organizer, {"prompt": "Licence", "kind": "choice", "choices": "MIT\nGPL", "required": True}
        )
        self.site = extras.save_question(self.ev, self.organizer, {"prompt": "Site", "kind": "url"})
        self.alice = User.objects.create_user("alice", "alice@example.org", "pw")
        self.team = event_services.create_team(self.ev, self.alice, "Nightshift")
        self.good = {self.why.id: "because", self.licence.id: "MIT"}

    def submitted(self):
        project = event_services.create_project(self.ev, self.team, self.alice, {"title": "P"})
        event_services.set_answers(project, self.alice, self.good)
        return event_services.submit_project(project, self.alice)


class AnswersTests(Open):
    def test_a_submitted_project_keeps_its_required_answers(self):
        project = self.submitted()
        for bad in ({self.why.id: ""}, {self.licence.id: None}, {self.why.id: "   "}):
            with self.assertRaises(ValidationError, msg=bad):
                event_services.set_answers(project, self.alice, bad)
        self.assertEqual(event_services.missing_answers(project), [])
        self.assertEqual(CustomAnswer.objects.get(project=project, question=self.why).value, "because")
        event_services.set_answers(project, self.alice, {self.why.id: "a better reason"})
        event_services.set_answers(project, self.organizer, {self.why.id: ""})  # organizers may

    def test_the_same_over_the_api(self):
        from accounts.models import ApiToken

        project = self.submitted()
        _, token = ApiToken.issue(self.alice, label="t")
        r = self.client.patch(
            f"/api/events/{self.ev.slug}/projects/{project.id}",
            json.dumps({"title": "P", "answers": {str(self.why.id): "", str(self.licence.id): None}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(event_services.missing_answers(project), [])

    def test_a_draft_may_leave_them_open(self):
        project = event_services.create_project(self.ev, self.team, self.alice, {"title": "P"})
        event_services.set_answers(project, self.alice, {self.why.id: ""})
        with self.assertRaises(ValidationError):
            event_services.submit_project(project, self.alice)

    def test_what_shows_as_nothing_is_no_answer(self):
        project = event_services.create_project(self.ev, self.team, self.alice, {"title": "P"})
        for blank in ("​", "ㅤ", "    ", "﻿‍", "⠀ᅠ"):
            event_services.set_answers(project, self.alice, {self.why.id: blank, self.licence.id: "MIT"})
            self.assertEqual(CustomAnswer.objects.get(project=project, question=self.why).value, "", repr(blank))
            with self.assertRaises(ValidationError, msg=repr(blank)):
                event_services.submit_project(project, self.alice)
        with self.assertRaises(ValidationError):
            event_services.set_answers(project, self.alice, {self.why.id: "a\x00b"})
        event_services.set_answers(project, self.alice, {self.why.id: "zero​width inside is text"})
        event_services.submit_project(project, self.alice)


class ThePageAndTheApiAgreeTests(Open):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.alice)
        self.new = f"/events/{self.ev.slug}/projects/new/"

    def post(self, **answers):
        data = {"title": "P", "save": "1", f"q_{self.why.id}": "because", f"q_{self.licence.id}": "MIT"}
        data.update({f"q_{k}": v for k, v in answers.items()})
        return self.client.post(self.new, data)

    def test_addresses(self):
        for bad in ("ftp://example.com/x", "example.com/x", "javascript:alert(1)"):
            r = self.post(**{str(self.site.id): bad})
            self.assertEqual(r.status_code, 200, bad)
            self.assertContains(r, "not an http or https address")
            with self.assertRaises(ValidationError):
                project = Project(event=self.ev, team=self.team, title="x")
                project.save()
                event_services.set_answers(project, self.alice, {self.site.id: bad})
            project.delete()
        self.assertFalse(Project.objects.filter(event=self.ev).exists())
        self.assertEqual(self.post(**{str(self.site.id): "https://example.com/x"}).status_code, 302)

    def test_length(self):
        r = self.post(**{str(self.why.id): "x" * 6000})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "longer than 5,000")
        self.assertFalse(Project.objects.filter(event=self.ev).exists())

    def test_a_draft_may_be_saved_without_and_the_form_still_marks_the_question(self):
        form = self.client.get(self.new)
        self.assertContains(form, 'Why <span class="muted" aria-hidden="true">*</span>', html=False)
        r = self.client.post(self.new, {"title": "P", "save": "1"})
        self.assertEqual(r.status_code, 302)
        project = Project.objects.get(event=self.ev)
        self.assertEqual(project.status, Project.Status.DRAFT)
        r = self.client.post(f"/events/{self.ev.slug}/projects/{project.id}/edit/", {"title": "P", "submit": "1"})
        self.assertEqual(r.status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.status, Project.Status.DRAFT)


class SpreadsheetTests(SeededTestCase):
    def test_what_a_spreadsheet_would_run_is_text(self):
        for cell in (
            "=1+1",
            " =1+1",
            "\n=1+1",
            " =1+1",
            "　=1+1",
            "﻿=1+1",
            "​=1+1",
            "＝1+1",
            "＋1+1",
            "－1+1",
            "＠SUM(A1)",
            "\t1",
            "\r1",
            "-1+1",
            "+1e5+cmd",
            "-",
        ):
            self.assertEqual(export.safe_cell(cell), "'" + cell, repr(cell))

    def test_numbers_and_plain_text_are_left_alone(self):
        for cell in ("-5", "+5", "-0.5000", "1,5", "-1,234.56", "-1e-05", "-.5", "-5%", " -5", "5", "plain", "a=b", ""):
            self.assertEqual(export.safe_cell(cell), cell, repr(cell))
        for value in (5, -5.5, None, True):
            self.assertIs(export.safe_cell(value), value)

    def test_each_part_of_a_joined_cell(self):
        self.assertEqual(
            export.joined(["a@example.org", "=HYPERLINK(1)", "django"]), "a@example.org;'=HYPERLINK(1);django"
        )
        project = Project.objects.filter(event=self.event).first()
        Project.objects.filter(pk=project.pk).update(tech_tags=["ok", "=cmd|' /C calc'!A0"])
        user = TeamMembership.objects.filter(team=project.team).first().user
        User.objects.filter(pk=user.pk).update(email="", username="+cmd")
        _, rows = export.projects_csv(self.event)
        row = next(r for r in rows[1:] if r[0] == project.id)
        self.assertIn(";'=cmd", row[12])
        self.assertIn("'+cmd", row[14])

    def test_a_bar_in_a_title_adds_no_column_to_the_reports(self):
        project = Project.objects.filter(event=self.event, title="Iron Switch").first()
        Project.objects.filter(pk=project.pk).update(title="Iron | Switch\nand more")
        for command in ("pairwise_report", "normalization_report"):
            out = StringIO()
            call_command(command, self.event.slug, stdout=out)
            line = next(x for x in out.getvalue().splitlines() if "Iron" in x and x.startswith("|"))
            header = next(x for x in out.getvalue().splitlines() if x.startswith("| ") and "project" in x.lower())
            self.assertIn("Iron \\| Switch and more", line, command)
            self.assertEqual(line.replace("\\|", "").count("|"), header.count("|"), command)


class SettingsTests(SeededTestCase):
    def test_the_site_address_is_checked_at_start(self):
        for good, reads in (
            ("", ""),
            ("https://judging.example.org", "https://judging.example.org"),
            (" http://localhost:8080/ ", "http://localhost:8080"),
            ("https://example.org/judging/", "https://example.org/judging"),
        ):
            self.assertEqual(site_address(good), reads)
        for bad in (
            "portal.example",
            "//portal.example",
            "ftp://portal.example",
            "javascript:alert(1)",
            "https://",
            "https://user:pw@portal.example",
            "https://portal.example/?next=x",
            "https://portal.example/#x",
            "https://portal.example:notaport",
            "https://portal .example",
            'https://portal.example/"><script>',
        ):
            with self.assertRaises(ImproperlyConfigured, msg=bad):
                site_address(bad)

    def test_a_busy_development_database_says_try_again(self):
        net = OutOfRangeMiddleware(lambda request: None)
        for path, kind in (("/api/events", "application/json"), ("/judge/", "text/plain")):
            response = net.process_exception(RequestFactory().post(path), OperationalError("database is locked"))
            self.assertEqual((response.status_code, response["Retry-After"]), (503, "1"))
            self.assertTrue(response["Content-Type"].startswith(kind))
        self.assertIsNone(net.process_exception(RequestFactory().get("/"), OperationalError("no such table")))
