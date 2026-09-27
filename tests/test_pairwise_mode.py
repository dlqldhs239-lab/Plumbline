"""Pairwise judging in the portal: who may compare what, which pair comes
next, and how the comparisons reach the results."""

from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction

from events import services as event_services
from events.models import EventRole, Role
from judging import pairwise_services as pw
from judging import services
from judging.models import JudgeAssignment, PairwiseComparison, ProjectResult

from .base import SeededTestCase


class Pairwise(SeededTestCase):
    """The seeded event with pairwise judging switched on. jdg_02 has six
    projects; the fixture's judging window is open."""

    def setUp(self):
        pw.set_enabled(self.event, self.organizer, True)
        self.judge = self.judge_b
        self.mine = pw.pool(self.judge, self.event)
        self.client.force_login(self.judge)
        self.page = f"/judge/{self.event.slug}/compare/"

    def answer_all(self, judge, prefer=max):
        n = 0
        while True:
            pair = pw.next_pair(judge, self.event)
            if pair is None:
                return n
            a, b = pair
            pw.record_comparison(judge, self.event, a.id, b.id, prefer(a.id, b.id))
            n += 1


class RulesTests(Pairwise):
    def test_the_pool_is_the_judges_own_assignments(self):
        own = set(JudgeAssignment.objects.filter(judge=self.judge).values_list("project_id", flat=True))
        self.assertEqual({p.id for p in self.mine}, own)
        self.assertEqual(len(self.mine), 6)
        self.assertEqual(pw.asked_for(6), 9)
        self.assertEqual([pw.asked_for(n) for n in (0, 1, 2, 3, 4)], [0, 0, 1, 3, 6])

    def test_only_assigned_projects_can_be_compared(self):
        a = self.mine[0]
        other = JudgeAssignment.objects.exclude(judge=self.judge).exclude(project__in=self.mine).first().project
        with self.assertRaises(PermissionDenied):
            pw.record_comparison(self.judge, self.event, a.id, other.id, a.id)
        with self.assertRaises(PermissionDenied):
            pw.record_comparison(self.participant, self.event, a.id, self.mine[1].id, a.id)
        with self.assertRaises(ValidationError):
            pw.record_comparison(self.judge, self.event, a.id, a.id, a.id)
        with self.assertRaises(ValidationError):
            pw.record_comparison(self.judge, self.event, a.id, self.mine[1].id, self.mine[2].id)
        self.assertEqual(PairwiseComparison.objects.count(), 0)

    def test_one_answer_per_judge_and_pair_whichever_way_round(self):
        a, b = pw.next_pair(self.judge, self.event)
        pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        for first, second in ((a.id, b.id), (b.id, a.id)):
            with self.assertRaises(ValidationError):
                pw.record_comparison(self.judge, self.event, first, second, b.id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PairwiseComparison.objects.create(event=self.event, judge=self.judge, left=b, right=a, preferred=a)
        self.assertEqual(PairwiseComparison.objects.count(), 1)

    def test_a_hidden_project_leaves_the_pool_and_the_ranking(self):
        a, b = pw.next_pair(self.judge, self.event)
        pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        c, d = pw.next_pair(self.judge, self.event)
        pw.record_comparison(self.judge, self.event, c.id, d.id, c.id)
        self.assertEqual(len({a.id, b.id, c.id, d.id}), 4)
        event_services.set_hidden(a, self.organizer, True)
        self.assertNotIn(a.id, {p.id for p in pw.pool(self.judge, self.event)})
        self.assertEqual(len(pw.collect(self.event)), 1)
        with self.assertRaises(PermissionDenied):
            pw.record_comparison(self.judge, self.event, a.id, c.id, c.id)

    def test_off_means_off(self):
        pw.set_enabled(self.event, self.organizer, False)
        a, b = self.mine[:2]
        with self.assertRaises(PermissionDenied):
            pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        with self.assertRaises(PermissionDenied):
            pw.set_enabled(self.event, self.judge, True)


class NextPairTests(Pairwise):
    def test_a_judge_is_asked_nine_times_and_never_twice_about_a_pair(self):
        seen = set()
        for _ in range(9):
            a, b = pw.next_pair(self.judge, self.event)
            key = tuple(sorted((a.id, b.id)))
            self.assertNotIn(key, seen)
            seen.add(key)
            pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        self.assertIsNone(pw.next_pair(self.judge, self.event))
        self.assertEqual(pw.status(self.judge, self.event), {"projects": 6, "done": 9, "asked": 9, "left": 0})

    def test_every_project_is_heard_about_before_any_is_heard_twice(self):
        first_three = []
        for _ in range(3):
            a, b = pw.next_pair(self.judge, self.event)
            first_three += [a.id, b.id]
            pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        self.assertEqual(sorted(first_three), sorted(p.id for p in self.mine))

    def test_the_next_pair_is_stable_until_answered(self):
        one = pw.next_pair(self.judge, self.event)
        two = pw.next_pair(self.judge, self.event)
        self.assertEqual([p.id for p in one], [p.id for p in two])

    def test_both_sides_get_used(self):
        lefts = 0
        for _ in range(9):
            a, b = pw.next_pair(self.judge, self.event)
            lefts += a.id < b.id
            pw.record_comparison(self.judge, self.event, a.id, b.id, None)
        self.assertTrue(0 < lefts < 9)


class ResultsTests(Pairwise):
    def test_comparisons_reach_the_results(self):
        judges = [r.user for r in EventRole.objects.filter(event=self.event, role=Role.JUDGE).select_related("user")]
        total = sum(self.answer_all(j) for j in judges)
        self.assertGreater(total, 40)
        out = services.recompute_results(self.event, self.organizer)
        self.assertEqual(out["comparisons"], total)
        rows = ProjectResult.objects.filter(event=self.event, pairwise_n__gt=0)
        self.assertEqual(sum(r.pairwise_n for r in rows), 2 * total)
        ranks = sorted(r.pairwise_rank for r in rows)
        self.assertEqual(ranks[0], 1)
        best = rows.order_by("pairwise_rank").first()
        self.assertGreater(best.pairwise_score, 0)
        summary = pw.summary(self.event, self.organizer)
        self.assertEqual((summary["comparisons"], summary["stale"]), (total, False))
        self.assertIsNotNone(summary["tau"])
        self.client.force_login(self.organizer)
        page = self.client.get(f"/events/{self.event.slug}/organize/results/")
        self.assertContains(page, "What the comparisons say")
        self.assertContains(page, best.project.title)

    def test_new_comparisons_make_the_summary_stale(self):
        services.recompute_results(self.event, self.organizer)
        a, b = pw.next_pair(self.judge, self.event)
        pw.record_comparison(self.judge, self.event, a.id, b.id, a.id)
        self.assertTrue(pw.summary(self.event, self.organizer)["stale"])

    def test_without_pairwise_the_results_are_as_before(self):
        pw.set_enabled(self.event, self.organizer, False)
        services.recompute_results(self.event, self.organizer)
        self.assertFalse(ProjectResult.objects.filter(event=self.event, pairwise_n__gt=0).exists())
        self.assertIsNone(pw.summary(self.event, self.organizer))
        with self.assertRaises(PermissionDenied):
            pw.summary(self.event, self.judge)


class PagesTests(Pairwise):
    def test_compare_by_form(self):
        r = self.client.get(self.page)
        self.assertContains(r, "Which is the better project?")
        a, b = r.context["pair"]
        r = self.client.post(self.page, {"first": a.pk, "second": b.pk, "preferred": b.pk})
        self.assertEqual(r.status_code, 302)
        row = PairwiseComparison.objects.get()
        self.assertEqual(row.preferred_id, b.pk)
        r = self.client.get(self.page)
        self.assertContains(r, "1 of 9 compared")
        a, b = r.context["pair"]
        self.client.post(self.page, {"first": a.pk, "second": b.pk, "preferred": "none"})
        self.assertIsNone(PairwiseComparison.objects.order_by("-id").first().preferred_id)
        self.assertTrue(self.event.audit_entries.filter(action="comparison.submit").exists())

    def test_nonsense_is_refused(self):
        a, b = self.mine[:2]
        for data in (
            {},
            {"first": "x", "second": b.pk, "preferred": b.pk},
            {"first": a.pk, "second": b.pk, "preferred": "both"},
            {"first": a.pk, "second": b.pk},
            {"first": a.pk, "second": "9" * 30, "preferred": a.pk},
            {"first": a.pk, "second": a.pk, "preferred": a.pk},
        ):
            r = self.client.post(self.page, data, follow=True)
            self.assertEqual(r.status_code, 200, data)
        self.assertEqual(PairwiseComparison.objects.count(), 0)

    def test_queue_offers_it_and_others_cannot_enter(self):
        self.assertContains(self.client.get(f"/judge/{self.event.slug}/"), "Compare in pairs")
        self.client.force_login(self.participant)
        self.assertEqual(self.client.get(self.page).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.page).status_code, 302)

    def test_console_switch(self):
        self.client.force_login(self.organizer)
        page = f"/events/{self.event.slug}/organize/rubric/"
        self.assertContains(self.client.get(page), "Stop asking for comparisons")
        self.client.post(page, {"pairwise": "off"})
        self.assertFalse(services.ensure_rubric(self.event).pairwise)
        self.assertTrue(self.event.audit_entries.filter(action="rubric.pairwise").exists())

    def test_api(self):
        base = f"/api/events/{self.event.slug}"
        r = self.client.get(f"{base}/pairs/next", **self.bearer("judge_b"))
        self.assertEqual((r.status_code, r.json()["asked"]), (200, 9))
        first, second = (s["project_id"] for s in r.json()["pair"])
        self.assertEqual(self.client.get(f"{base}/pairs/next", **self.bearer("participant")).status_code, 403)
        body = {"first": first, "second": second, "preferred": first}
        r = self.client.post(
            f"{base}/comparisons", data=body, content_type="application/json", **self.bearer("judge_b")
        )
        self.assertEqual(r.status_code, 201, r.content)
        r = self.client.post(
            f"{base}/comparisons", data=body, content_type="application/json", **self.bearer("judge_b")
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            f"{base}/comparisons", data=body, content_type="application/json", **self.bearer("judge_a")
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get(f"{base}/pairwise").status_code, 403)
        services.recompute_results(self.event, self.organizer)
        r = self.client.get(f"{base}/pairwise", **self.bearer("organizer"))
        self.assertEqual([x["rank"] for x in r.json()], [1, 2])
        services.publish_results(self.event, self.organizer)
        self.assertEqual(self.client.get(f"{base}/pairwise").status_code, 200)


class ReportTests(SeededTestCase):
    def test_report_on_the_fixtures_uses_the_implied_comparisons(self):
        out = StringIO()
        call_command("pairwise_report", self.event.slug, stdout=out)
        text = out.getvalue()
        self.assertIn("implied by the rubric scores", text)
        self.assertIn("bradley-terry-mm-v1", text)
        self.assertIn("| adjusted rubric score |", text)
        self.assertIn("settled", text)
        self.assertNotIn("did not settle", text)
        self.assertIn("Iron Switch", text)
