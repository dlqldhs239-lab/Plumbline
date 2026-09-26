"""Every page renders for the role that should see it, and refuses the ones
that should not."""

from judging.models import JudgeAssignment

from .base import SeededTestCase


class PublicPagesTests(SeededTestCase):
    def test_public_pages(self):
        s = self.event.slug
        for url in ["/", f"/events/{s}/", f"/events/{s}/gallery/", f"/events/{s}/gallery/?q=glass", f"/accounts/login/", "/accounts/signup/", "/api/docs", "/api/openapi.json"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        project = self.event.projects.filter(status="submitted").first()
        self.assertEqual(self.client.get(project.get_absolute_url()).status_code, 200)

    def test_login_with_email(self):
        r = self.client.post("/accounts/login/", {"username": "organizer@example.org", "password": "plumbline"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_login_required_redirects(self):
        for url in ["/dashboard/", f"/events/{self.event.slug}/organize/", "/judge/", "/accounts/tokens/"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/accounts/login/", r["Location"])


class OrganizerPagesTests(SeededTestCase):
    def test_organizer_console(self):
        self.client.force_login(self.organizer)
        s = self.event.slug
        for tail in ["", "settings/", "projects/", "judges/", "rubric/", "assignments/", "results/", "audit/", "audit/?action=seed"]:
            r = self.client.get(f"/events/{s}/organize/{tail}")
            self.assertEqual(r.status_code, 200, tail)
        for kind in ["projects", "assignments", "scores", "results", "calibration", "audit"]:
            r = self.client.get(f"/events/{s}/organize/export/{kind}.csv")
            self.assertEqual(r.status_code, 200, kind)
            self.assertIn(",", r.content.decode().splitlines()[0])
        r = self.client.post(f"/events/{s}/organize/results/", {"action": "recompute"})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(f"/events/{s}/organize/results/"), "Judge calibration")

    def test_judge_and_participant_cannot_open_console(self):
        for user in (self.judge_a, self.participant):
            self.client.force_login(user)
            self.assertEqual(self.client.get(f"/events/{self.event.slug}/organize/").status_code, 403)
            self.assertEqual(self.client.get(f"/events/{self.event.slug}/organize/export/scores.csv").status_code, 403)


class JudgePagesTests(SeededTestCase):
    def test_judge_console_shows_only_own(self):
        self.client.force_login(self.judge_b)
        s = self.event.slug
        self.assertEqual(self.client.get("/judge/").status_code, 200)
        r = self.client.get(f"/judge/{s}/")
        self.assertEqual(r.status_code, 200)
        own = JudgeAssignment.objects.filter(judge=self.judge_b)
        other = JudgeAssignment.objects.exclude(judge=self.judge_b).first()
        for a in own:
            self.assertContains(r, a.project.title)
        self.assertEqual(self.client.get(f"/judge/{s}/review/{own.first().pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/judge/{s}/review/{other.pk}/").status_code, 403)

    def test_participant_is_not_a_judge(self):
        self.client.force_login(self.participant)
        self.assertEqual(self.client.get(f"/judge/{self.event.slug}/").status_code, 403)


class ParticipantPagesTests(SeededTestCase):
    def test_dashboard_and_team_pages(self):
        self.client.force_login(self.participant)
        r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Glass Signal")
        team = self.participant.team_memberships.first().team
        self.assertEqual(self.client.get(f"/events/{self.event.slug}/teams/{team.pk}/").status_code, 200)
        # Someone else's team page is not visible.
        other_team = self.event.teams.exclude(pk=team.pk).first()
        self.assertEqual(self.client.get(f"/events/{self.event.slug}/teams/{other_team.pk}/").status_code, 403)

    def test_edit_refused_after_deadline_in_ui(self):
        self.client.force_login(self.participant)
        project = self.participant.team_memberships.first().team.projects.first()
        r = self.client.post(f"/events/{self.event.slug}/projects/{project.pk}/edit/", {"title": "changed", "track": project.track_id})
        self.assertEqual(r.status_code, 200)  # form re-rendered with the error
        self.assertContains(r, "closed")
        project.refresh_from_db()
        self.assertNotEqual(project.title, "changed")
