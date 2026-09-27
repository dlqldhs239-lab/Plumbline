"""Bugs found by driving the portal in a real browser on 2026-09-27. Each test
is named for what went wrong."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import ApiToken
from audit.services import plain, record
from events import services
from events.models import Event, EventRole, Project, Role, Team, TeamMembership
from judging import services as judging_services
from judging.models import JudgeAssignment

from .base import SeededTestCase


class FreshEventCase(SeededTestCase):
    """An event that is open now, with one team and one project."""

    def setUp(self):
        now = timezone.now()
        self.open_event = Event.objects.create(
            slug="open-now",
            name="Open Now",
            submissions_open_at=now - timedelta(days=1),
            submissions_close_at=now + timedelta(days=1),
        )
        EventRole.objects.create(event=self.open_event, user=self.organizer, role=Role.ORGANIZER)
        self.track = self.open_event.tracks.create(name="Tools")
        self.alice = User.objects.create_user("alice", "alice@example.org", "pw")
        self.team = services.create_team(self.open_event, self.alice, "Nightshift")
        self.project = services.create_project(
            self.open_event,
            self.team,
            self.alice,
            {"title": "Quiet Hours", "track": self.track, "repo_url": "https://example.org/r"},
        )
        _, self.alice_token = ApiToken.issue(self.alice)


class RubricSaveTests(FreshEventCase):
    def test_saving_the_rubric_in_the_console_does_not_crash(self):
        """The audit entry could not store a Decimal weight: HTTP 500."""
        self.client.force_login(self.organizer)
        url = f"/events/{self.open_event.slug}/organize/rubric/"
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {"c-TOTAL_FORMS": "2", "c-INITIAL_FORMS": "0", "c-MIN_NUM_FORMS": "0", "c-MAX_NUM_FORMS": "1000"}
        data.update(
            {"c-0-key": "impact", "c-0-name": "Impact", "c-0-weight": "2.50", "c-0-description": "Does it matter"}
        )
        data.update({"c-1-key": "craft", "c-1-name": "Craft", "c-1-weight": "1", "c-1-description": ""})
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 302)
        rubric = judging_services.ensure_rubric(self.open_event)
        self.assertEqual(
            [(c.key, c.weight) for c in rubric.criteria.all()],
            [("impact", Decimal("2.50")), ("craft", Decimal("1.00"))],
        )
        entry = self.open_event.audit_entries.filter(action="rubric.replace").first()
        self.assertEqual(entry.detail["criteria"][0]["weight"], "2.50")

    def test_audit_detail_accepts_anything(self):
        detail = {"when": timezone.now(), "weight": Decimal("1.5"), "who": self.alice, "nested": [{"d": Decimal("2")}]}
        self.assertEqual(plain(detail)["weight"], "1.5")
        record("test.anything", event=self.open_event, detail=detail)


class OneSubmissionPerTeamTests(FreshEventCase):
    def test_second_submission_is_refused_with_a_reason(self):
        services.submit_project(self.project, self.alice)
        second = services.create_project(
            self.open_event, self.team, self.alice, {"title": "Another", "track": self.track}
        )
        r = self.client.post(
            f"/api/events/{self.open_event.slug}/projects/{second.pk}/submit",
            HTTP_AUTHORIZATION=f"Bearer {self.alice_token}",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("already submitted", r.json()["detail"])
        second.refresh_from_db()
        self.assertEqual(second.status, Project.Status.DRAFT)
        # Withdrawing the first makes room.
        services.withdraw_project(self.project, self.alice)
        services.submit_project(second, self.alice)
        self.assertEqual(Project.objects.filter(team=self.team, status=Project.Status.SUBMITTED).count(), 1)

    def test_resubmitting_the_same_project_is_fine(self):
        services.submit_project(self.project, self.alice)
        first = self.project.submitted_at
        services.submit_project(self.project, self.alice)
        self.assertEqual(self.project.submitted_at, first)

    def test_shared_repository_is_shown_to_organizers_only(self):
        services.submit_project(self.project, self.alice)
        bob = User.objects.create_user("bob", "bob@example.org", "pw")
        other = services.create_team(self.open_event, bob, "Daybreak")
        twin = services.create_project(
            self.open_event, other, bob, {"title": "Hours", "track": self.track, "repo_url": "https://EXAMPLE.org/r"}
        )
        services.submit_project(twin, bob)
        self.assertEqual(list(services.lookalikes(twin)), [self.project])
        self.client.force_login(self.organizer)
        self.assertContains(self.client.get(twin.get_absolute_url()), "Shares a repository")
        self.client.force_login(bob)
        self.assertNotContains(self.client.get(twin.get_absolute_url()), "Shares a repository")


class FeedbackTests(SeededTestCase):
    def setUp(self):
        self.project = Project.objects.get(external_id="prj_01")
        self.member = self.project.team.memberships.first().user
        judging_services.recompute_results(self.event, self.organizer)

    def test_team_sees_feedback_only_after_publication(self):
        self.client.force_login(self.member)
        page = self.client.get(self.project.get_absolute_url())
        self.assertNotContains(page, "Feedback from the judges")
        self.assertNotContains(page, "By criterion")
        judging_services.publish_results(self.event, self.organizer)
        page = self.client.get(self.project.get_absolute_url())
        self.assertContains(page, "Feedback from the judges")
        self.assertContains(page, "By criterion")
        self.assertContains(page, "Runs clean.")

    def test_feedback_never_names_a_judge(self):
        judging_services.publish_results(self.event, self.organizer)
        self.client.force_login(self.member)
        html = self.client.get(self.project.get_absolute_url()).content.decode()
        for a in JudgeAssignment.objects.filter(project=self.project).select_related("judge"):
            self.assertNotIn(a.judge.username, html)
            self.assertNotIn(a.judge.email, html)
            if a.judge.last_name:
                self.assertNotIn(a.judge.last_name, html)

    def test_other_teams_and_visitors_get_scores_but_not_comments(self):
        judging_services.publish_results(self.event, self.organizer)
        page = self.client.get(self.project.get_absolute_url())
        self.assertContains(page, "By criterion")
        self.assertNotContains(page, "Feedback from the judges")
        self.assertNotContains(page, "Runs clean.")
        outsider = TeamMembership.objects.exclude(team=self.project.team).first().user
        self.client.force_login(outsider)
        self.assertNotContains(self.client.get(self.project.get_absolute_url()), "Runs clean.")

    def test_judge_order_in_feedback_is_not_the_assignment_order(self):
        labels = [f["label"] for f in judging_services.feedback_for(self.project)]
        self.assertEqual(labels, [f"Judge {i}" for i in range(1, len(labels) + 1)])


class TeamRuleTests(FreshEventCase):
    def test_blank_team_name_and_second_team(self):
        bob = User.objects.create_user("bob", "bob@example.org", "pw")
        self.client.force_login(bob)
        url = f"/events/{self.open_event.slug}/teams/new/"
        self.assertEqual(self.client.post(url, {"name": "   "}).status_code, 200)
        self.assertEqual(Team.objects.filter(event=self.open_event).count(), 1)
        self.client.force_login(self.alice)
        r = self.client.post(url, {"name": "Second"})
        self.assertContains(r, "already on a team")
