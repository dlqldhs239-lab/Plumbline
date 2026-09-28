"""The Open House event, the audit log as it is read, and what a visitor to a
sample installation is shown."""

from django.core.management import call_command
from django.test import override_settings

from audit import reading
from audit.models import AuditLog
from community.models import Comment, Vote, Voter
from events.models import Event, Project
from judging import pairwise_services as pw
from judging import services as judging
from judging.models import JudgeAssignment, PairwiseComparison, ProjectResult
from records.models import Record

from .base import FIXTURES, SeededTestCase


class OpenHouse(SeededTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command("seed_open_house", quiet=True)
        cls.house = Event.objects.get(external_id="open_house")


class SeedTests(OpenHouse):
    def test_every_window_is_open(self):
        e = self.house
        self.assertTrue(e.submissions_open() and e.judging_open() and e.voting_open())
        self.assertFalse(e.results_published)
        self.assertEqual(e.slug, "open-house")

    def test_there_is_something_of_everything(self):
        e = self.house
        self.assertEqual(e.projects.filter(status=Project.Status.SUBMITTED).count(), 8)
        self.assertEqual(e.projects.filter(status=Project.Status.DRAFT).count(), 1)
        self.assertTrue(all(p.description and p.tagline and p.tech_tags for p in judging.eligible_projects(e)))
        self.assertEqual(e.prizes.count(), 3)
        self.assertEqual(e.custom_questions.count(), 2)
        self.assertEqual(JudgeAssignment.objects.filter(event=e).count(), 24)
        self.assertEqual(Comment.objects.filter(project__event=e).count(), 5)
        self.assertEqual(Voter.objects.filter(event=e).count(), 18)
        self.assertGreater(Vote.objects.filter(event=e).count(), 18)
        self.assertGreater(PairwiseComparison.objects.filter(event=e).count(), 10)
        self.assertEqual(ProjectResult.objects.filter(event=e, rank__isnull=False).count(), 8)
        submitted = e.projects.filter(status=Project.Status.SUBMITTED).select_related("team")
        self.assertFalse([p.title for p in submitted if p.team.name == p.title])

    def test_the_seeded_judges_have_work_waiting(self):
        for judge in (self.judge_a, self.judge_b):
            mine = JudgeAssignment.objects.filter(event=self.house, judge=judge)
            self.assertEqual(mine.count(), 3)
            self.assertFalse(mine.filter(status=JudgeAssignment.Status.SUBMITTED).exists())
            self.assertIsNotNone(pw.next_pair(judge, self.house))
        self.client.force_login(self.judge_b)
        self.assertContains(self.client.get(f"/judge/{self.house.slug}/"), "Compare in pairs")

    def test_four_ballots_from_one_address_are_flagged(self):
        flagged = [v.flags for v in Voter.objects.filter(event=self.house).order_by("id") if v.flags]
        self.assertEqual(len(flagged), 3)
        self.assertIn("many_ballots_same_ip", flagged[-1])

    def test_the_participant_of_the_checker_stays_a_participant(self):
        self.assertFalse(self.house.roles.filter(user=self.participant).exists())
        r = self.client.get("/api/judges/me/scores", **self.bearer("participant"))
        self.assertEqual(r.status_code, 403)

    def test_it_is_loaded_once_and_is_the_same_each_time(self):
        before = (AuditLog.objects.count(), Project.objects.count(), Vote.objects.count())
        call_command("seed_open_house", quiet=True)
        self.assertEqual((AuditLog.objects.count(), Project.objects.count(), Vote.objects.count()), before)
        self.assertEqual(Event.objects.filter(external_id="open_house").count(), 1)
        order = [r.project.title for r in judging.placed(self.house)]
        self.assertEqual((order[0], order[-1]), ("Roll Call", "Green Room"))

    def test_the_same_seed_gives_the_same_assignment_whatever_order_the_judges_come_in(self):
        from unittest import mock

        def pairs():
            rows = JudgeAssignment.objects.filter(event=self.house).values_list("judge_id", "project_id")
            return sorted(rows)

        first = pairs()
        real = judging.judges_for
        for order in (lambda rows: rows[::-1], lambda rows: rows[3:] + rows[:3]):
            JudgeAssignment.objects.filter(event=self.house).delete()
            with mock.patch.object(judging, "judges_for", lambda e, order=order: order(list(real(e)))):
                judging.assign_balanced(self.house, self.organizer, 3, batch="again", seed=7)
            self.assertEqual(pairs(), first)

    def test_the_fixture_event_is_untouched_and_both_are_on_the_front_page(self):
        self.assertEqual(self.event.projects.count(), 41)
        page = self.client.get("/")
        self.assertContains(page, "Sample Hack 2026")
        self.assertContains(page, "Open House")

    def test_the_organizer_can_publish_it(self):
        self.assertEqual(self.client.get(f"/events/{self.house.slug}/results/").status_code, 403)
        judging.publish_results(self.house, self.organizer)
        self.assertEqual(self.client.get(f"/events/{self.house.slug}/results/").status_code, 200)

    def test_without_the_fixture_organizer_there_is_no_open_house(self):
        Event.objects.filter(external_id="open_house").delete()
        self.organizer.email = "someone.else@example.org"
        self.organizer.save()
        call_command("seed_open_house", quiet=True)
        self.assertFalse(Event.objects.filter(external_id="open_house").exists())


class PublishedSeedTests(SeededTestCase):
    def test_a_finished_event_has_given_out_its_records(self):
        self.assertEqual(Record.objects.count(), 0)
        Event.objects.all().delete()
        call_command("seed_fixtures", str(FIXTURES), quiet=True, publish=True)
        event = Event.objects.get(external_id="evt_01")
        self.assertTrue(event.results_published)
        self.assertGreater(Record.objects.filter(event=event, kind="placement").count(), 2)
        self.assertGreater(Record.objects.filter(event=event, kind="judge").count(), 20)


class ReadingTests(OpenHouse):
    def setUp(self):
        self.client.force_login(self.organizer)
        self.page = f"/events/{self.house.slug}/organize/audit/"

    def test_the_log_reads_as_sentences(self):
        r = self.client.get(self.page)
        self.assertContains(r, "Sample Organizer</strong> computed the results")
        self.assertContains(r, "at first start")
        self.assertNotContains(r, "{&#x27;")
        self.assertNotContains(r, "pairwisecomparison")
        r = self.client.get(self.page + "?action=comparison.submit")
        self.assertRegex(r.content.decode(), r"(preferred [A-Z][a-z]+ [A-Za-z]+ to [A-Z]|could not choose between)")
        self.assertNotContains(r, "computed the results")

    def test_it_can_be_read_by_person(self):
        r = self.client.get(self.page + "?actor=bruno.costa")
        self.assertContains(r, "<strong>Bruno Costa</strong>")
        self.assertNotContains(r, "<strong>Hiro Tanaka</strong>")
        r = self.client.get(self.page + "?actor=" + "x" * 5000 + "&action=%00")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Nothing logged that matches")

    def test_a_change_is_shown_as_what_it_was_and_what_it_became(self):
        judging.set_jury_k(judging.ensure_rubric(self.house), self.organizer, 5)
        entry = AuditLog.objects.filter(event=self.house, action="rubric.jury_k").first()
        row = reading.read(entry)
        self.assertEqual(row["did"], "changed the jury-size adjustment")
        self.assertEqual(row["facts"], [{"label": "value", "was": "none", "value": "5"}])

    def test_anything_can_be_read(self):
        odd = (None, "text", 7, [1, 2], {"a": {"b": [1, {"c": None}]}}, {"changed": "odd"}, {"x" * 300: "y" * 300})
        for detail in odd:
            row = reading.read(AuditLog(action="something.new_thing", detail=detail, channel="cli"))
            self.assertEqual(
                (row["who"], row["did"], row["via"]), ("The portal", "something new thing", "from the command line")
            )
            self.assertLessEqual(len(row["facts"]), 12)
        self.assertEqual(reading.read(AuditLog(action="voter.admit", channel="ui"))["who"], "A visitor")

    def test_a_name_in_the_log_is_text(self):
        Project.objects.filter(event=self.house, title="Quorum").update(title="<script>alert(1)</script>")
        r = self.client.get(self.page + "?action=comparison.submit")
        self.assertNotContains(r, "<script>alert(1)</script>")

    def test_others_cannot_read_it(self):
        self.client.force_login(self.judge_b)
        self.assertEqual(self.client.get(self.page).status_code, 403)


class PagesTests(OpenHouse):
    def test_the_ballot_is_a_list_to_mark(self):
        r = self.client.get(f"/events/{self.house.slug}/ballot/")
        self.assertContains(r, 'class="ballot"')
        self.assertContains(r, "Late Train")
        self.assertNotContains(r, "placeholder")
        project = judging.eligible_projects(self.house).first()
        r = self.client.post(f"/events/{self.house.slug}/projects/{project.pk}/vote/", {"weight": 1}, follow=True)
        self.assertContains(r, "Voted. Remove")
        self.assertContains(r, 'aria-pressed="true"')

    def test_the_organizer_sees_the_flagged_ballots_first(self):
        self.client.force_login(self.organizer)
        r = self.client.get(f"/events/{self.house.slug}/organize/voting/")
        self.assertContains(r, "4 ballots from one address")
        text = r.content.decode()
        ballots = text[text.index("Came by") :]
        first_row = ballots[ballots.index("<tbody>") :][:1200]
        self.assertIn("badge warn", first_row)

    def test_an_event_without_a_vote_says_so_instead_of_forty_noughts(self):
        self.client.force_login(self.organizer)
        r = self.client.get(f"/events/{self.event.slug}/organize/voting/")
        self.assertContains(r, "This event has no community vote")
        self.assertNotContains(r, "The tally so far")

    def test_a_project_without_a_description_shows_its_result_where_the_text_would_be(self):
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        bare = judging.eligible_projects(self.event).first()
        text = self.client.get(bare.get_absolute_url()).content.decode()
        self.assertLess(text.index('id="result"'), text.index('class="cover rail"'))
        self.assertEqual(text.count('id="result"'), 1)
        judging.publish_results(self.house, self.organizer)
        full = judging.eligible_projects(self.house).first()
        text = self.client.get(full.get_absolute_url()).content.decode()
        self.assertGreater(text.index('id="result"'), text.index('class="cover rail"'))
        self.assertEqual(text.count('id="result"'), 1)


class SampleLoginTests(SeededTestCase):
    @override_settings(PLUMBLINE_SAMPLE_SECRETS=True)
    def test_a_sample_installation_says_who_to_sign_in_as(self):
        r = self.client.get("/accounts/login/")
        self.assertContains(r, "Sample installation")
        self.assertContains(r, 'data-login="wei.lindqvist@example.org"')
        self.assertNotContains(self.client.get("/"), "data-login")
        self.admin.is_active = False
        self.admin.save()
        self.assertNotContains(self.client.get("/accounts/login/"), "admin@example.org")

    @override_settings(PLUMBLINE_SAMPLE_SECRETS=False)
    def test_a_real_installation_does_not(self):
        r = self.client.get("/accounts/login/")
        self.assertNotContains(r, "Sample installation")
        self.assertNotContains(r, "example.org")
        self.assertNotContains(r, "data-login")
