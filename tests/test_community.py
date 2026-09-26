"""T3: community voting, comments, hidden results, random ballot order,
anti-abuse. Uses a fresh open event so the voting window is live."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import ApiToken
from community.models import Comment, Vote, Voter
from community.services import ballot_projects
from events.models import Event, EventRole, Project, Role, Team, TeamMembership


class VotingTestCase(TestCase):
    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.org = User.objects.create_user("org", "org@example.org", "pw")
        self.event = Event.objects.create(
            slug="vote-hack",
            name="Vote Hack",
            submissions_open_at=now - timedelta(days=2),
            submissions_close_at=now - timedelta(days=1),
            voting_access=Event.VotingAccess.OPEN,
            voting_open_at=now - timedelta(hours=1),
            voting_close_at=now + timedelta(days=1),
        )
        EventRole.objects.create(event=self.event, user=self.org, role=Role.ORGANIZER)
        self.projects = []
        for i in range(6):
            owner = User.objects.create_user(f"u{i}", f"u{i}@example.org", "pw")
            team = Team.objects.create(event=self.event, name=f"Team {i}")
            TeamMembership.objects.create(team=team, user=owner)
            self.projects.append(
                Project.objects.create(event=self.event, team=team, title=f"Project {i}", status=Project.Status.SUBMITTED, submitted_at=now - timedelta(days=1, hours=i))
            )
        _, self.org_token = ApiToken.issue(self.org)


class OpenVotingTests(VotingTestCase):
    def test_open_ballot_sets_cookie_and_votes_toggle(self):
        c = Client()
        r = c.get("/events/vote-hack/ballot/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"pl_voter_{self.event.pk}", r.cookies)
        self.assertEqual(Voter.objects.count(), 1)
        p = self.projects[0]
        r = c.post(f"/events/vote-hack/projects/{p.pk}/vote/", {"weight": 1})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Vote.objects.filter(project=p).count(), 1)
        c.post(f"/events/vote-hack/projects/{p.pk}/vote/", {"weight": 0})
        self.assertEqual(Vote.objects.filter(project=p).count(), 0)
        # Reloading the ballot does not create a second voter.
        c.get("/events/vote-hack/ballot/")
        self.assertEqual(Voter.objects.count(), 1)

    def test_one_vote_per_project_in_plain_mode(self):
        c = Client()
        c.get("/events/vote-hack/ballot/")
        p = self.projects[0]
        c.post(f"/events/vote-hack/projects/{p.pk}/vote/", {"weight": 3})
        self.assertEqual(Vote.objects.filter(project=p).count(), 0)

    def test_ballot_order_is_random_per_voter_and_stable(self):
        a = [p.id for p in ballot_projects(self.event, "voter-a")]
        a_again = [p.id for p in ballot_projects(self.event, "voter-a")]
        b = [p.id for p in ballot_projects(self.event, "voter-b")]
        self.assertEqual(a, a_again)
        self.assertEqual(sorted(a), sorted(b))
        self.assertNotEqual(a, b)

    def test_duplicate_ballots_from_one_address_are_flagged(self):
        for _ in range(4):
            Client().get("/events/vote-hack/ballot/")  # each fresh client = no cookie = new ballot
        voters = Voter.objects.order_by("id")
        self.assertEqual(voters.count(), 4)
        self.assertIn("shared_ip", voters.last().flags)
        self.assertIn("many_ballots_same_ip", voters.last().flags)
        self.assertEqual(voters.first().flags, [])

    def test_admission_rate_limit(self):
        for _ in range(10):
            self.assertEqual(Client().get("/events/vote-hack/ballot/").status_code, 200)
        self.assertEqual(Client().get("/events/vote-hack/ballot/").status_code, 403)

    def test_voided_ballot_is_excluded_from_tally(self):
        c = Client()
        c.get("/events/vote-hack/ballot/")
        p = self.projects[1]
        c.post(f"/events/vote-hack/projects/{p.pk}/vote/", {"weight": 1})
        voter = Voter.objects.get()
        from community.services import tally, void_voter

        self.assertEqual(tally(self.event)[p.id]["votes"], 1)
        void_voter(voter, self.org, "test")
        self.assertNotIn(p.id, tally(self.event))
        self.assertEqual(Vote.objects.count(), 1)  # kept, not deleted
        r = c.post(f"/events/vote-hack/projects/{p.pk}/vote/", {"weight": 1})
        self.assertEqual(r.status_code, 302)  # refused with a message, no new vote weight change
        self.assertEqual(Vote.objects.get().weight, 1)

    def test_results_and_tally_hidden_during_voting(self):
        c = Client()
        self.assertEqual(c.get("/events/vote-hack/results/").status_code, 403)
        self.assertEqual(c.get("/api/events/vote-hack/votes/summary").status_code, 401)
        u = User.objects.create_user("x", "x@example.org", "pw")
        _, t = ApiToken.issue(u)
        self.assertEqual(c.get("/api/events/vote-hack/votes/summary", HTTP_AUTHORIZATION=f"Bearer {t}").status_code, 403)
        r = c.get("/api/events/vote-hack/votes/summary", HTTP_AUTHORIZATION=f"Bearer {self.org_token}")
        self.assertEqual(r.status_code, 200)
        page = c.get("/events/vote-hack/ballot/").content.decode()
        self.assertNotIn("Votes</th>", page)

    def test_voting_closed_outside_window(self):
        self.event.voting_close_at = timezone.now() - timedelta(minutes=1)
        self.event.save()
        r = Client().get("/events/vote-hack/ballot/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "not open")
        self.assertEqual(Voter.objects.count(), 0)


class QuadraticVotingTests(VotingTestCase):
    def setUp(self):
        super().setUp()
        self.event.voting_credits = 9
        self.event.voting_access = Event.VotingAccess.AUTHENTICATED
        self.event.save()
        self.voter_user = User.objects.create_user("v", "v@example.org", "pw")
        _, self.token = ApiToken.issue(self.voter_user)

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def test_budget_is_enforced_quadratically(self):
        p0, p1 = self.projects[0], self.projects[1]
        r = self.client.post(f"/api/events/vote-hack/projects/{p0.pk}/vote", data={"weight": 2}, content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 200, r.content)  # cost 4
        r = self.client.post(f"/api/events/vote-hack/projects/{p1.pk}/vote", data={"weight": 2}, content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 200)  # cost 8 total
        r = self.client.post(f"/api/events/vote-hack/projects/{self.projects[2].pk}/vote", data={"weight": 2}, content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 400)  # would be 12 > 9
        r = self.client.post(f"/api/events/vote-hack/projects/{self.projects[2].pk}/vote", data={"weight": 1}, content_type="application/json", **self.auth())
        self.assertEqual(r.status_code, 200)  # exactly 9
        r = self.client.get("/api/events/vote-hack/ballot", **self.auth())
        self.assertEqual(r.json()["credits_used"], 9)
        self.assertEqual(sum(1 for i in r.json()["items"] if i["my_weight"]), 3)

    def test_anonymous_cannot_vote_in_auth_mode(self):
        c = Client()
        r = c.get("/events/vote-hack/ballot/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])
        self.assertEqual(Voter.objects.count(), 0)


class EmailVotingTests(VotingTestCase):
    def setUp(self):
        super().setUp()
        self.event.voting_access = Event.VotingAccess.EMAIL
        self.event.save()

    def test_ballot_links(self):
        self.client.force_login(self.org)
        r = self.client.post("/events/vote-hack/organize/voting/", {"action": "issue", "emails": "a@example.org\nb@example.org\nnot-an-email\na@example.org"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Voter.objects.filter(kind="email").count(), 2)
        r = self.client.get("/events/vote-hack/organize/voting/ballot-links.csv")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.content.decode().splitlines()), 3)
        self.client.logout()
        # Without a link: refused. With the link: cookie bound, votes work.
        c = Client()
        self.assertEqual(c.get("/events/vote-hack/ballot/").status_code, 403)
        token = Voter.objects.get(email="a@example.org").ballot_token
        r = c.get(f"/events/vote-hack/ballot/{token}/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/events/vote-hack/ballot/").status_code, 200)
        c.post(f"/events/vote-hack/projects/{self.projects[0].pk}/vote/", {"weight": 1})
        self.assertEqual(Vote.objects.get().voter.email, "a@example.org")
        self.assertEqual(Client().get("/events/vote-hack/ballot/bogus-token/").status_code, 403)


class CommentTests(VotingTestCase):
    def test_comments_require_login_and_can_be_hidden(self):
        p = self.projects[0]
        r = self.client.post(f"/events/vote-hack/projects/{p.pk}/comments/", {"body": "hi"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Comment.objects.count(), 0)
        u = User.objects.create_user("c", "c@example.org", "pw")
        self.client.force_login(u)
        self.client.post(f"/events/vote-hack/projects/{p.pk}/comments/", {"body": "Nice work"})
        self.client.post(f"/events/vote-hack/projects/{p.pk}/comments/", {"body": "Nice work"})  # exact duplicate ignored
        self.assertEqual(Comment.objects.count(), 1)
        self.assertContains(self.client.get(p.get_absolute_url()), "Nice work")
        c = Comment.objects.get()
        # A participant cannot hide; an organizer can, and the body disappears for others.
        self.assertEqual(self.client.post(f"/events/vote-hack/projects/{p.pk}/comments/{c.pk}/hide/").status_code, 403)
        self.client.force_login(self.org)
        self.client.post(f"/events/vote-hack/projects/{p.pk}/comments/{c.pk}/hide/", {"hidden": "1"})
        self.client.logout()
        page = Client().get(p.get_absolute_url())
        self.assertNotContains(page, "Nice work")
        r = Client().get(f"/api/events/vote-hack/projects/{p.pk}/comments")
        self.assertEqual(r.json(), [])

    def test_comment_rate_limit(self):
        u = User.objects.create_user("spam", "spam@example.org", "pw")
        _, t = ApiToken.issue(u)
        p = self.projects[0]
        codes = []
        for i in range(12):
            r = self.client.post(f"/api/events/vote-hack/projects/{p.pk}/comments", data={"body": f"msg {i}"}, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {t}")
            codes.append(r.status_code)
        self.assertEqual(codes[:10], [201] * 10)
        self.assertEqual(codes[10:], [403, 403])
