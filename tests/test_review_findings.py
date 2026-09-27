"""Bugs found by an independent code review on 2026-09-27. Each test is named
for what could go wrong, and fails on the code as it was before the fix."""

import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from accounts.models import ApiToken, SignInLink
from community import services as community_services
from community.models import Vote, Voter
from events import services
from events.models import CustomQuestion, Event, EventRole, Project, Role, Team, Track
from integrations import services as hook_services
from integrations.models import Webhook
from judging import services as judging_services
from judging.models import Criterion, JudgeAssignment, ProjectResult, Score

from .base import FIXTURES, SeededTestCase


class Base(SeededTestCase):
    """The seeded (closed) event plus an event that is open now."""

    def setUp(self):
        cache.clear()
        now = timezone.now()
        self.open_event = Event.objects.create(
            slug="open-now",
            name="Open Now",
            submissions_open_at=now - timedelta(days=1),
            submissions_close_at=now + timedelta(days=1),
            judging_open_at=now - timedelta(days=1),
            voting_access=Event.VotingAccess.OPEN,
            voting_credits=9,
        )
        EventRole.objects.create(event=self.open_event, user=self.organizer, role=Role.ORGANIZER)
        self.tools = self.open_event.tracks.create(name="Tools")
        self.games = self.open_event.tracks.create(name="Games")
        self.alice = User.objects.create_user("alice", "alice@example.org", "pw")
        self.team = services.create_team(self.open_event, self.alice, "Nightshift")
        self.project = services.create_project(
            self.open_event, self.team, self.alice, {"title": "Quiet Hours", "track": self.tools}
        )
        _, self.alice_token = ApiToken.issue(self.alice)
        self.mallory = User.objects.create_user("mallory", "mallory@example.org", "pw")
        _, self.mallory_token = ApiToken.issue(self.mallory)

    def api(self, method, path, token, data=None):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        if data is not None:
            kwargs.update(data=json.dumps(data), content_type="application/json")
        return getattr(self.client, method)(f"/api{path}", **kwargs)

    def own_event(self, user, slug="mallory-hack"):
        return services.create_event(
            user,
            {
                "name": slug,
                "submissions_open_at": timezone.now() - timedelta(days=1),
                "submissions_close_at": timezone.now() + timedelta(days=1),
            },
        )


class AccountTakeoverTests(Base):
    def test_an_invited_judge_cannot_be_claimed_through_another_event(self):
        """Anyone may create an event. Adding someone else's invited judge to
        it must not give them that judge's account."""
        victim = services.add_judge(self.event, self.organizer, "invited@example.org", "In Vited")
        theirs = self.own_event(self.mallory)
        role = services.add_judge(theirs, self.mallory, "invited@example.org")
        self.assertEqual(role.user, victim.user)
        self.assertFalse(services.can_issue_sign_in_link(self.mallory, role.user))
        with self.assertRaises(ValidationError):
            services.issue_judge_link(role, self.mallory)
        r = self.api("post", f"/events/{theirs.slug}/judges/{role.user.username}/sign-in-link", self.mallory_token)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SignInLink.objects.filter(user=victim.user).count(), 0)

    def test_the_inviting_organizers_link_survives_the_attempt(self):
        victim = services.add_judge(self.event, self.organizer, "invited@example.org")
        raw = services.issue_judge_link(victim, self.organizer)
        theirs = self.own_event(self.mallory)
        role = services.add_judge(theirs, self.mallory, "invited@example.org")
        with self.assertRaises(ValidationError):
            services.issue_judge_link(role, self.mallory)
        self.assertIsNotNone(SignInLink.find(raw))

    def test_the_real_organizer_still_can(self):
        role = services.add_judge(self.event, self.organizer, "invited@example.org")
        self.assertTrue(services.can_issue_sign_in_link(self.organizer, role.user))

    def test_staff_and_token_holders_are_never_handed_out(self):
        theirs = self.own_event(self.mallory)
        shell = User.objects.create(username="ops", email="ops@example.org", is_staff=True)
        shell.set_unusable_password()
        shell.save()
        role = services.add_judge(theirs, self.mallory, "ops@example.org")
        self.assertFalse(services.can_issue_sign_in_link(self.mallory, role.user))
        bot = User.objects.create(username="bot", email="bot@example.org")
        bot.set_unusable_password()
        bot.save()
        ApiToken.issue(bot)
        role = services.add_judge(theirs, self.mallory, "bot@example.org")
        self.assertFalse(services.can_issue_sign_in_link(self.mallory, role.user))

    def test_the_link_is_not_kept_in_the_session(self):
        role = services.add_judge(self.event, self.organizer, "invited@example.org")
        self.client.force_login(self.organizer)
        page = f"/events/{self.event.slug}/organize/judges/"
        r = self.client.post(page, {"link": role.pk})
        self.assertContains(r, "/accounts/claim/")
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertNotIn("claim", json.dumps(dict(self.client.session)))
        self.assertNotContains(self.client.get(page), "/accounts/claim/")

    def test_bad_judge_input_is_refused_not_crashed(self):
        for payload in (
            {"email": "not-an-email"},
            {"email": "a@b"},
            {"email": "ok@example.org", "tracks": [999999]},
            {"email": "ok@example.org", "tracks": [self.tools.pk]},  # a track of another event
            {"email": "ok@example.org", "name": "n" * 400},
            {"email": ("x" * 300) + "@example.org"},
        ):
            r = self.api("post", f"/events/{self.event.slug}/judges", self.tokens["organizer"], payload)
            self.assertEqual(r.status_code, 400, payload)
        self.assertFalse(User.objects.filter(email="ok@example.org").exists())


class TrackIsolationTests(Base):
    def setUp(self):
        super().setUp()
        services.submit_project(self.project, self.alice)
        self.judge = User.objects.create_user("jude", "jude@example.org", "pw")
        _, self.judge_token = ApiToken.issue(self.judge)
        self.role = EventRole.objects.create(event=self.open_event, user=self.judge, role=Role.JUDGE)
        self.assignment = judging_services.assign_manual(self.open_event, self.organizer, self.judge, self.project)
        # The judge is moved to another track after the assignment was made.
        self.role.tracks.set([self.games])

    def test_every_way_in_honours_the_track(self):
        a = self.assignment
        self.assertEqual(self.api("get", f"/judges/me/assignments/{a.pk}", self.judge_token).status_code, 403)
        r = self.api("get", "/judges/me/scores", self.judge_token)
        self.assertEqual(r.json()["assignments"], [])
        r = self.api("get", f"/judges/me/scores?event={self.open_event.slug}", self.judge_token)
        self.assertEqual(r.json()["assignments"], [])
        r = self.api(
            "post",
            f"/judges/me/assignments/{a.pk}/scores",
            self.judge_token,
            {"scores": {"functionality": 5, "quality": 5, "innovation": 5}, "submit": True},
        )
        self.assertEqual(r.status_code, 403)
        with self.assertRaises(PermissionDenied):
            judging_services.save_scores(a, self.judge, {"functionality": 5})
        self.assertEqual(Score.objects.filter(assignment=a).count(), 0)
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(f"/judge/{self.open_event.slug}/review/{a.pk}/").status_code, 403)

    def test_a_review_url_of_another_event_is_refused(self):
        self.role.tracks.clear()
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(f"/judge/{self.event.slug}/review/{self.assignment.pk}/").status_code, 403)

    def test_manual_assignment_honours_tracks_and_eligibility(self):
        bob = User.objects.create_user("bob", "bob@example.org", "pw")
        team = services.create_team(self.open_event, bob, "Daybreak")
        draft = services.create_project(self.open_event, team, bob, {"title": "Draft", "track": self.games})
        with self.assertRaisesMessage(ValidationError, "Only submitted projects"):
            judging_services.assign_manual(self.open_event, self.organizer, self.judge, draft)
        JudgeAssignment.objects.all().filter(pk=self.assignment.pk).delete()
        with self.assertRaisesMessage(ValidationError, "restricted to other tracks"):
            judging_services.assign_manual(self.open_event, self.organizer, self.judge, self.project)
        foreign = Project.objects.filter(event=self.event).first()
        with self.assertRaises(ValidationError):
            judging_services.assign_manual(self.open_event, self.organizer, self.judge, foreign)

    def test_a_withdrawn_project_leaves_the_queue(self):
        self.role.tracks.clear()
        self.assertEqual(judging_services.assignments_for_judge(self.judge, self.open_event).count(), 1)
        services.set_hidden(self.project, self.organizer, True)
        self.assertEqual(judging_services.assignments_for_judge(self.judge, self.open_event).count(), 0)
        with self.assertRaises(PermissionDenied):
            judging_services.save_scores(self.assignment, self.judge, {"functionality": 5})

    def test_balanced_assignment_survives_a_removed_judge_with_history(self):
        """Reviews by someone who is no longer a judge used to crash the next run."""
        self.role.tracks.clear()
        self.role.delete()
        other = User.objects.create_user("june", "june@example.org", "pw")
        EventRole.objects.create(event=self.open_event, user=other, role=Role.JUDGE)
        out = judging_services.assign_balanced(self.open_event, self.organizer, 2, seed=1)
        self.assertEqual(out["created"], 1)
        self.assertNotIn(self.judge.id, [a.judge_id for a in JudgeAssignment.objects.filter(batch=out["batch"])])


class ScoreInputTests(Base):
    def setUp(self):
        super().setUp()
        services.submit_project(self.project, self.alice)
        self.judge = User.objects.create_user("jude", "jude@example.org", "pw")
        EventRole.objects.create(event=self.open_event, user=self.judge, role=Role.JUDGE)
        self.assignment = judging_services.assign_manual(self.open_event, self.organizer, self.judge, self.project)
        self.client.force_login(self.judge)
        self.page = f"/judge/{self.open_event.slug}/review/{self.assignment.pk}/"

    def test_a_score_that_is_not_a_number_is_a_message(self):
        for bad in ("abc", "4.5", "9" * 40, "-1", "0", "6", "²"):
            r = self.client.post(self.page, {"score_functionality": bad, "comment": ""}, follow=True)
            self.assertEqual(r.status_code, 200, bad)
        self.assertEqual(Score.objects.filter(assignment=self.assignment).count(), 0)
        r = self.client.post(self.page, {"score_functionality": "abc"}, follow=True)
        self.assertContains(r, "must be a whole number")


class VotingTests(Base):
    def setUp(self):
        super().setUp()
        services.submit_project(self.project, self.alice)
        bob = User.objects.create_user("bob", "bob@example.org", "pw")
        team = services.create_team(self.open_event, bob, "Daybreak")
        self.second = services.create_project(self.open_event, team, bob, {"title": "Hours", "track": self.games})
        services.submit_project(self.second, bob)

    def vote(self, project, weight, token=None):
        return self.api(
            "post",
            f"/events/{self.open_event.slug}/projects/{project.pk}/vote",
            token or self.mallory_token,
            {"weight": weight},
        )

    def test_api_votes_in_open_mode_share_one_ballot(self):
        """Each call used to get a new ballot, so the budget never ran out."""
        self.assertEqual(self.vote(self.project, 2).status_code, 200)  # 4 of 9 credits
        self.assertEqual(self.vote(self.second, 2).status_code, 200)  # 8 of 9
        r = self.vote(self.project, 3)  # would be 9 + 4
        self.assertEqual(r.status_code, 400)
        self.assertIn("Not enough credits", r.json()["detail"])
        self.assertEqual(Voter.objects.filter(event=self.open_event).count(), 1)
        self.assertEqual(Voter.objects.get(event=self.open_event).user, self.mallory)
        r = self.api("get", f"/events/{self.open_event.slug}/ballot", self.mallory_token)
        self.assertEqual(r.json()["credits_used"], 8)

    def test_browser_and_token_of_one_account_are_one_ballot(self):
        self.vote(self.project, 2)
        self.client.force_login(self.mallory)
        self.client.get(f"/events/{self.open_event.slug}/ballot/")
        r = self.client.post(f"/events/{self.open_event.slug}/projects/{self.second.pk}/vote/", {"weight": "3"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Voter.objects.filter(event=self.open_event).count(), 1)
        self.assertEqual(Vote.objects.filter(event=self.open_event).count(), 1)

    def test_a_ballot_opened_before_signing_in_becomes_the_accounts(self):
        c = Client()
        c.get(f"/events/{self.open_event.slug}/ballot/")
        c.force_login(self.mallory)
        c.get(f"/events/{self.open_event.slug}/ballot/")
        self.assertEqual(Voter.objects.get(event=self.open_event).user, self.mallory)
        self.vote(self.project, 1)
        self.assertEqual(Voter.objects.filter(event=self.open_event).count(), 1)

    def test_a_weight_that_is_not_a_number_is_a_message(self):
        c = Client()
        c.get(f"/events/{self.open_event.slug}/ballot/")
        for bad in ("abc", "1.5", "", "9" * 40, "-2", "²"):
            r = c.post(f"/events/{self.open_event.slug}/projects/{self.project.pk}/vote/", {"weight": bad})
            self.assertEqual(r.status_code, 302, bad)
        self.assertEqual(Vote.objects.count(), 0)

    def test_integrity_report_counts_ballots_not_votes(self):
        self.vote(self.project, 1)
        self.vote(self.second, 1)
        self.vote(self.project, 1, self.alice_token)
        report = community_services.integrity_report(self.open_event)
        self.assertEqual(report["by_kind"], {"open": 2})
        self.assertEqual(report["total"], 2)

    def test_throttle_survives_the_window_ending_between_two_calls(self):
        request = mock.Mock(META={"REMOTE_ADDR": "203.0.113.9"})
        with mock.patch.object(cache, "add", side_effect=[False, True]):
            with mock.patch.object(cache, "incr", side_effect=ValueError("gone")):
                community_services.throttle(request, "test")


@override_settings(PLUMBLINE_TRUST_PROXY=False)
class ClientAddressTests(Base):
    def test_a_forged_forwarded_header_is_ignored(self):
        self.client.force_login(self.organizer)
        self.client.post(
            f"/events/{self.open_event.slug}/organize/projects/{self.project.pk}/hide/",
            {"hidden": "1"},
            HTTP_X_FORWARDED_FOR="not-an-address, 10.0.0.1",
            REMOTE_ADDR="203.0.113.7",
        )
        entry = self.open_event.audit_entries.filter(action="project.hide").first()
        self.assertEqual(entry.ip_address, "203.0.113.7")

    @override_settings(PLUMBLINE_TRUST_PROXY=True)
    def test_behind_a_proxy_the_header_is_used_and_garbage_is_dropped(self):
        from plumbline.inputs import client_ip

        self.assertEqual(client_ip(mock.Mock(META={"HTTP_X_FORWARDED_FOR": "198.51.100.4, 10.0.0.1"})), "198.51.100.4")
        self.assertEqual(client_ip(mock.Mock(META={"HTTP_X_FORWARDED_FOR": "<script>", "REMOTE_ADDR": "x"})), "")


class EventCreationTests(Base):
    def create(self, **over):
        payload = {
            "name": "Winter Build",
            "submissions_open_at": "2027-01-01T00:00:00Z",
            "submissions_close_at": "2027-01-03T00:00:00Z",
        }
        payload.update(over)
        return self.api("post", "/events", self.mallory_token, payload)

    def test_a_refused_event_leaves_nothing_behind(self):
        before = Event.objects.count()
        cases = (
            {"tracks": ["x" * 200]},
            {"reviews_per_project": -1},
            {"reviews_per_project": 0},
            {"reviews_per_project": 99999},
            {"name": "n" * 500},
            {"name": "   "},
            {"slug": "new"},
            {"slug": self.open_event.slug},
            {"submissions_close_at": "2026-12-01T00:00:00Z"},
            {"judging_open_at": "2027-02-02T00:00:00Z", "judging_close_at": "2027-02-01T00:00:00Z"},
        )
        for over in cases:
            r = self.create(**over)
            self.assertEqual(r.status_code, 400, (over, r.content))
        self.assertEqual(Event.objects.count(), before)
        self.assertFalse(Track.objects.filter(name="x" * 200).exists())

    def test_repeated_track_names_are_one_track(self):
        r = self.create(tracks=["Tools", "tools", " Tools ", "Games", ""])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual([t["name"] for t in r.json()["tracks"]], ["Tools", "Games"])

    def test_a_name_with_no_latin_letters_gets_a_slug(self):
        r = self.create(name="겨울 해커톤")
        self.assertEqual(r.status_code, 201, r.content)
        slug = r.json()["slug"]
        self.assertTrue(slug.startswith("event-"))
        self.assertEqual(self.client.get(f"/events/{slug}/").status_code, 200)
        self.client.force_login(self.mallory)
        r = self.client.post(
            "/events/new/",
            {
                "name": "봄 해커톤",
                "submissions_open_at": "2027-01-01T00:00",
                "submissions_close_at": "2027-01-03T00:00",
                "reviews_per_project": "3",
                "voting_access": "closed",
                "voting_credits": "0",
                "theme_ground": "#080C18",
                "theme_ink": "#E9EEFF",
                "theme_accent": "#00E5D0",
                "theme_signal": "#FF3D6E",
            },
        )
        self.assertEqual(r.status_code, 302, r.content[:3000])
        made = Event.objects.get(name="봄 해커톤")
        self.assertTrue(made.slug.startswith("event-"))
        self.assertTrue(EventRole.objects.filter(event=made, user=self.mallory, role=Role.ORGANIZER).exists())
        self.assertEqual(made.audit_entries.get(action="event.create").actor, self.mallory)

    def test_the_slug_new_cannot_hide_the_create_page(self):
        self.client.force_login(self.organizer)
        with self.assertRaises(ValidationError):
            services.clean_slug("new")
        with self.assertRaises(ValidationError):
            services.update_event(self.open_event, self.organizer, {"slug": "new"})


class EventSettingsTests(Base):
    def patch(self, data):
        return self.api("patch", f"/events/{self.open_event.slug}", self.tokens["organizer"], data)

    def test_null_where_a_value_is_needed_is_refused(self):
        for field in ("submissions_close_at", "submissions_open_at", "name", "reviews_per_project", "voting_credits"):
            r = self.patch({field: None})
            self.assertEqual(r.status_code, 400, field)
        self.assertEqual(self.patch({"judging_close_at": None}).status_code, 200)
        for data in ({"reviews_per_project": 0}, {"voting_credits": -5}, {"voting_access": "whatever"}):
            self.assertEqual(self.patch(data).status_code, 400, data)

    def test_the_settings_page_goes_through_the_audited_service(self):
        self.client.force_login(self.organizer)
        page = f"/events/{self.open_event.slug}/organize/settings/"
        e = self.open_event
        data = {
            "name": "Open Now",
            "slug": e.slug,
            "tagline": "A new line",
            "description": "",
            "submissions_open_at": e.submissions_open_at.strftime("%Y-%m-%dT%H:%M"),
            "submissions_close_at": e.submissions_close_at.strftime("%Y-%m-%dT%H:%M"),
            "reviews_per_project": "4",
            "voting_access": "open",
            "voting_credits": "9",
            "comments_enabled": "on",
            "is_listed": "on",
            "theme_ground": e.theme_ground,
            "theme_ink": e.theme_ink,
            "theme_accent": e.theme_accent,
            "theme_signal": e.theme_signal,
            "tracks_text": "Tools\nGames\nHardware",
        }
        r = self.client.post(page, data)
        self.assertEqual(r.status_code, 302, r.content[:3000])
        entry = e.audit_entries.filter(action="event.update").first()
        self.assertEqual(entry.actor, self.organizer)
        self.assertEqual(entry.detail["changed"]["reviews_per_project"], [3, 4])
        self.assertEqual(entry.detail["changed"]["tagline"], ["", "A new line"])
        self.assertEqual(e.audit_entries.get(action="event.tracks.add").detail["tracks"], ["Hardware"])
        self.assertEqual(e.tracks.count(), 3)
        data["slug"] = "new"
        r = self.client.post(page, data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "used by the portal itself")


class RubricTests(Base):
    def put(self, rows):
        return self.api("put", f"/events/{self.open_event.slug}/rubric", self.tokens["organizer"], rows)

    def test_a_rubric_nobody_could_score_with_is_refused(self):
        good = {"key": "impact", "name": "Impact", "weight": 1}
        cases = (
            [],
            [good, good],
            [good, {"key": "IMPACT", "name": "Again"}],
            [{"key": "has space", "name": "X"}],
            [{"key": "", "name": "X"}],
            [{"key": "k" * 60, "name": "X"}],
            [{"key": "ok", "name": ""}],
            [{"key": "ok", "name": "X", "weight": -1}],
            [{"key": "ok", "name": "X", "weight": 0}],
            [{"key": "ok", "name": "X", "weight": 1e12}],
        )
        judging_services.ensure_rubric(self.open_event)
        before = list(Criterion.objects.filter(rubric__event=self.open_event).values_list("key", flat=True))
        for rows in cases:
            r = self.put(rows)
            self.assertEqual(r.status_code, 400, (rows, r.content))
        after = list(Criterion.objects.filter(rubric__event=self.open_event).values_list("key", flat=True))
        self.assertEqual(before, after)
        self.assertEqual(self.put([good, {"key": "craft", "name": "Craft", "weight": 0}]).status_code, 200)

    def test_the_console_says_why(self):
        self.client.force_login(self.organizer)
        data = {"c-TOTAL_FORMS": "2", "c-INITIAL_FORMS": "0", "c-MIN_NUM_FORMS": "0", "c-MAX_NUM_FORMS": "1000"}
        data.update({"c-0-key": "impact", "c-0-name": "Impact", "c-0-weight": "1", "c-0-description": ""})
        data.update({"c-1-key": "impact", "c-1-name": "Impact again", "c-1-weight": "1", "c-1-description": ""})
        r = self.client.post(f"/events/{self.open_event.slug}/organize/rubric/", data)
        self.assertContains(r, "used twice")


class SubmissionTests(Base):
    def test_a_refused_submit_leaves_no_orphan_draft(self):
        services.submit_project(self.project, self.alice)
        before = Project.objects.filter(team=self.team).count()
        self.client.force_login(self.alice)
        r = self.client.post(
            f"/events/{self.open_event.slug}/projects/new/",
            {"title": "Second try", "track": self.tools.pk, "submit": "1"},
        )
        self.assertContains(r, "already submitted")
        self.assertEqual(Project.objects.filter(team=self.team).count(), before)
        r = self.api(
            "post",
            f"/events/{self.open_event.slug}/projects",
            self.alice_token,
            {"title": "Third try", "track_id": self.tools.pk, "submit": True},
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Project.objects.filter(team=self.team).count(), before)

    def test_a_refused_submit_does_not_keep_the_edit_either(self):
        services.submit_project(self.project, self.alice)
        draft = services.create_project(self.open_event, self.team, self.alice, {"title": "B", "track": self.tools})
        r = self.api(
            "patch",
            f"/events/{self.open_event.slug}/projects/{draft.pk}",
            self.alice_token,
            {"title": "B renamed", "submit": True},
        )
        self.assertEqual(r.status_code, 400)
        draft.refresh_from_db()
        self.assertEqual(draft.title, "B")

    def test_a_track_of_another_event_is_an_error_not_a_silent_drop(self):
        foreign = Track.objects.filter(event=self.event).first()
        r = self.api(
            "patch",
            f"/events/{self.open_event.slug}/projects/{self.project.pk}",
            self.alice_token,
            {"title": "Quiet Hours", "track_id": foreign.pk},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("does not belong", r.json()["detail"])
        self.project.refresh_from_db()
        self.assertEqual(self.project.track, self.tools)

    def test_no_new_teams_after_the_deadline(self):
        late = User.objects.create_user("late", "late@example.org", "pw")
        with self.assertRaises(PermissionDenied):
            services.create_team(self.event, late, "Too Late")
        _, token = ApiToken.issue(late)
        r = self.client.post(f"/api/events/{self.event.slug}/teams?name=Too+Late", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Team.objects.filter(name="Too Late").exists())

    def test_overlong_and_out_of_range_input(self):
        r = self.client.post(
            f"/api/events/{self.open_event.slug}/teams?name={'n' * 300}",
            HTTP_AUTHORIZATION=f"Bearer {self.mallory_token}",
        )
        self.assertEqual(r.status_code, 400)
        for query in ("max_uses=0", "max_uses=99999999", "ttl_hours=-1", "ttl_hours=99999999999"):
            r = self.client.post(
                f"/api/events/{self.open_event.slug}/teams/{self.team.pk}/invites?{query}",
                HTTP_AUTHORIZATION=f"Bearer {self.alice_token}",
            )
            self.assertEqual(r.status_code, 400, query)

    def test_custom_question_help_text_is_escaped(self):
        CustomQuestion.objects.create(
            event=self.open_event, prompt="Licence", help_text="<script>alert(1)</script>", kind="text"
        )
        self.client.force_login(self.alice)
        r = self.client.get(f"/events/{self.open_event.slug}/projects/{self.project.pk}/edit/")
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;alert(1)&lt;/script&gt;")


class NumbersInAddressesTests(Base):
    HUGE = "9" * 30

    def test_pages(self):
        slug = self.open_event.slug
        self.client.force_login(self.organizer)
        for path in (
            f"/events/{slug}/gallery/?track={self.HUGE}",
            f"/events/{slug}/gallery/?track=²",
            f"/events/{slug}/gallery/?track=abc",
            f"/events/{slug}/projects/{self.HUGE}/",
            f"/events/{slug}/teams/{self.HUGE}/",
        ):
            self.assertIn(self.client.get(path).status_code, (200, 404), path)
        self.assertNotContains(self.client.get(f"/events/{slug}/gallery/?track=abc"), "Quiet Hours")

    def test_organizer_forms(self):
        slug = self.open_event.slug
        self.client.force_login(self.organizer)
        for page, data in (
            (f"/events/{slug}/organize/judges/", {"link": "abc"}),
            (f"/events/{slug}/organize/judges/", {"remove": self.HUGE}),
            (f"/events/{slug}/organize/voting/", {"action": "void", "voter": "x"}),
            (f"/events/{slug}/organize/voting/", {"action": "void", "voter": ""}),
            (f"/events/{slug}/organize/integrations/", {"action": "delete", "webhook": "1;drop"}),
            (f"/events/{slug}/organize/integrations/", {"action": "retry", "delivery": self.HUGE}),
        ):
            self.assertEqual(self.client.post(page, data).status_code, 404, (page, data))

    def test_api(self):
        slug = self.open_event.slug
        token = self.tokens["organizer"]
        for path in (
            f"/events/{slug}/projects?track={self.HUGE}",
            f"/events/{slug}/projects/{self.HUGE}",
            f"/events/{slug}/audit?before_id={self.HUGE}",
            f"/judges/{self.HUGE}/scores",
            f"/judges/me/assignments/{self.HUGE}",
        ):
            self.assertIn(self.api("get", path, token).status_code, (200, 403, 404, 422), path)
        r = self.api("post", f"/events/{slug}/assignments/auto", token, {"reviews_per_project": 10**12, "seed": 10**30})
        self.assertEqual(r.status_code, 400)


class PublishedResultsTests(SeededTestCase):
    def test_a_project_hidden_after_publication_leaves_the_results(self):
        judging_services.recompute_results(self.event, self.organizer)
        judging_services.publish_results(self.event, self.organizer)
        row = ProjectResult.objects.filter(event=self.event, rank_normalized=1).first()
        title = row.project.title
        self.assertContains(self.client.get(f"/events/{self.event.slug}/results/"), title)
        services.set_hidden(row.project, self.organizer, True)
        self.assertNotContains(self.client.get(f"/events/{self.event.slug}/results/"), title)
        ids = [r["project_id"] for r in self.client.get(f"/api/events/{self.event.slug}/results").json()]
        self.assertNotIn(row.project_id, ids)

    def test_unranked_projects_come_last(self):
        judging_services.recompute_results(self.event, self.organizer)
        ProjectResult.objects.filter(event=self.event, rank=1).update(rank=None, rank_normalized=None, rank_raw=None)
        ranks = [r.rank for r in ProjectResult.objects.filter(event=self.event)]
        self.assertIsNone(ranks[-1])
        self.assertIsNotNone(ranks[0])
        ranks = [r.rank for r in judging_services.standings(self.event)]
        self.assertIsNone(ranks[-1])


class EnumerationTests(SeededTestCase):
    def test_a_stranger_cannot_tell_which_usernames_exist(self):
        outsider = User.objects.create_user("outsider", "outsider@example.org", "pw")
        _, token = ApiToken.issue(outsider)
        answers = set()
        for ref in (self.judge_a.username, "no-such-person", "999999", "jdg_01"):
            r = self.client.get(f"/api/judges/{ref}/scores", HTTP_AUTHORIZATION=f"Bearer {token}")
            answers.add((r.status_code, r.content))
        self.assertEqual(len(answers), 1)
        self.assertEqual(next(iter(answers))[0], 403)
        r = self.client.get("/api/judges/no-such-person/scores", **self.bearer("organizer"))
        self.assertEqual(r.status_code, 404)


@override_settings(PLUMBLINE_WEBHOOKS_ASYNC=False)
class WebhookDestinationTests(SeededTestCase):
    INTERNAL = (
        "http://127.0.0.1:8000/x",
        "http://localhost/x",
        "http://LOCALHOST./x",
        "http://app.localhost/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.17.0.1/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://0.0.0.0/x",
        "http://2130706433/x",
        "http://127.1/x",
        "http://0x7f000001/x",
        "http://db.internal/x",
        "http://db:5432/x",
        "http://user:pw@receiver.example.org/x",
        "http://receiver.example.org:99999/x",
    )

    def test_internal_addresses_are_refused(self):
        for url in self.INTERNAL:
            with self.assertRaises(ValidationError, msg=url):
                hook_services.create_webhook(self.event, self.organizer, url)
        self.assertEqual(Webhook.objects.count(), 0)

    def test_a_name_that_resolves_inwards_is_refused_when_sending(self):
        hook = hook_services.create_webhook(self.event, self.organizer, "https://rebind.example.org/hook")
        inward = [(2, 1, 6, "", ("10.1.2.3", 443))]
        with mock.patch("integrations.services.socket.getaddrinfo", return_value=inward):
            with mock.patch("urllib.request.OpenerDirector.open") as sent:
                d = hook_services.send_test(hook, self.organizer)
        sent.assert_not_called()
        self.assertEqual(d.status, "failed")
        self.assertIn("private addresses", d.error)

    @override_settings(PLUMBLINE_WEBHOOK_ALLOW_PRIVATE=True)
    def test_an_operator_can_allow_their_own_network(self):
        hook_services.create_webhook(self.event, self.organizer, "http://10.0.0.5/hook")

    def test_the_secret_is_shown_in_full_once(self):
        r = self.client.post(
            f"/api/events/{self.event.slug}/webhooks",
            data={"url": "https://receiver.example.org/a"},
            content_type="application/json",
            **self.bearer("organizer"),
        )
        secret = r.json()["secret"]
        listed = self.client.get(f"/api/events/{self.event.slug}/webhooks", **self.bearer("organizer")).json()
        self.assertNotEqual(listed[0]["secret"], secret)
        self.assertTrue(secret.startswith(listed[0]["secret"].rstrip("…")))
        self.client.force_login(self.organizer)
        self.assertNotContains(self.client.get(f"/events/{self.event.slug}/organize/integrations/"), secret)


class SeedSafetyTests(SeededTestCase):
    def seed(self):
        out = StringIO()
        call_command("seed_fixtures", str(FIXTURES), stdout=out)
        return out.getvalue()

    def test_a_second_run_changes_nothing(self):
        assignment = JudgeAssignment.objects.filter(event=self.event, status="submitted").first()
        score = assignment.scores.order_by("-criterion__order").first()
        score.value = 1 if score.value != 1 else 2
        score.save()
        assignment.comment = "Edited by the organizer"
        assignment.save()
        role = EventRole.objects.get(event=self.event, external_id="jdg_01")
        role.tracks.clear()
        judging_services.ensure_rubric(self.event).criteria.first().delete()
        counts = [m.objects.count() for m in (User, Event, Project, JudgeAssignment, Score, Criterion, ApiToken)]
        audit = self.event.audit_entries.count()
        self.seed()
        self.assertEqual(
            counts, [m.objects.count() for m in (User, Event, Project, JudgeAssignment, Score, Criterion, ApiToken)]
        )
        self.assertEqual(self.event.audit_entries.count(), audit)
        score.refresh_from_db()
        assignment.refresh_from_db()
        self.assertIn(score.value, (1, 2))
        self.assertEqual(assignment.comment, "Edited by the organizer")
        self.assertEqual(role.tracks.count(), 0)

    def test_a_deleted_admin_is_not_brought_back_or_replaced(self):
        self.admin.delete()
        squatter = User.objects.create_user("squatter", "admin@example.org", "pw")
        self.seed()
        squatter.refresh_from_db()
        self.assertFalse(squatter.is_superuser)
        self.assertFalse(squatter.is_staff)
        self.assertEqual(User.objects.filter(is_superuser=True).count(), 0)

    def test_a_revoked_seed_token_is_reported_as_revoked(self):
        raw = ApiToken.deterministic_raw("org")
        ApiToken.objects.filter(key_hash=ApiToken.hash_key(raw)).update(revoked_at=timezone.now())
        text = self.seed()
        self.assertNotIn(raw, text)
        self.assertIn("revoked", text)
        self.assertIn(ApiToken.deterministic_raw("jdg_a"), text)


class FirstSeedTests(Base):
    def test_a_taken_slug_and_an_existing_address_do_not_stop_or_corrupt_the_first_load(self):
        Event.objects.filter(pk=self.event.pk).delete()
        User.objects.filter(email__in=["admin@example.org", "organizer@example.org"]).delete()
        squatter = User.objects.create_user("squatter", "admin@example.org", "pw")
        mine = services.create_event(
            self.mallory,
            {
                "name": "Sample Hack 2026",
                "submissions_open_at": timezone.now(),
                "submissions_close_at": timezone.now() + timedelta(days=1),
            },
        )
        self.assertEqual(mine.slug, "sample-hack-2026")
        call_command("seed_fixtures", str(FIXTURES), quiet=True)
        seeded = Event.objects.get(external_id="evt_01")
        self.assertNotEqual(seeded.slug, mine.slug)
        self.assertEqual(seeded.projects.count(), 41)
        squatter.refresh_from_db()
        self.assertFalse(squatter.is_superuser)


class SignupTests(SeededTestCase):
    def test_a_username_cannot_look_like_an_email(self):
        r = self.client.post(
            "/accounts/signup/",
            {
                "username": "tomas.varga@example.org",
                "email": "someone.else@example.org",
                "password1": "a-Long-passphrase-9",
                "password2": "a-Long-passphrase-9",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "cannot contain @")
        self.assertFalse(User.objects.filter(email="someone.else@example.org").exists())
