"""Signing in without email: one-time links, who may create them, and the
small things around accounts that go wrong in real use."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from accounts.models import SignInLink
from events import services
from events.models import EventRole, Role

from .base import SeededTestCase


class SignInLinkTests(SeededTestCase):
    def invite(self, email="new.judge@example.org"):
        return services.add_judge(self.event, self.organizer, email, "New Judge")

    def test_invited_judge_claims_the_account(self):
        role = self.invite()
        self.assertFalse(role.user.has_usable_password())
        raw = services.issue_judge_link(role, self.organizer)
        c = Client()
        page = c.get(f"/accounts/claim/{raw}/")
        self.assertContains(page, "new.judge@example.org")
        r = c.post(
            f"/accounts/claim/{raw}/", {"new_password1": "a-Long-passphrase-9", "new_password2": "a-Long-passphrase-9"}
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get(f"/judge/{self.event.slug}/").status_code, 200)
        # The link worked once.
        self.assertEqual(Client().get(f"/accounts/claim/{raw}/").status_code, 410)
        r = Client().post("/accounts/login/", {"username": "new.judge@example.org", "password": "a-Long-passphrase-9"})
        self.assertEqual(r.status_code, 302)
        from audit.models import AuditLog

        self.assertTrue(AuditLog.objects.filter(action="account.claim", actor=role.user).exists())

    def test_weak_password_is_refused_and_the_link_survives(self):
        raw = services.issue_judge_link(self.invite(), self.organizer)
        c = Client()
        r = c.post(f"/accounts/claim/{raw}/", {"new_password1": "123", "new_password2": "123"})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(SignInLink.find(raw))

    def test_a_new_link_cancels_the_old_one(self):
        role = self.invite()
        first = services.issue_judge_link(role, self.organizer)
        second = services.issue_judge_link(role, self.organizer)
        self.assertIsNone(SignInLink.find(first))
        self.assertIsNotNone(SignInLink.find(second))

    def test_expired_and_unknown_links(self):
        role = self.invite()
        raw = services.issue_judge_link(role, self.organizer)
        SignInLink.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(f"/accounts/claim/{raw}/").status_code, 410)
        self.assertEqual(self.client.get("/accounts/claim/not-a-token/").status_code, 410)

    def test_organizer_cannot_take_over_an_existing_account(self):
        """Inviting someone who already has an account must not be a way in."""
        victim = self.participant  # has a password
        role = services.add_judge(self.event, self.organizer, victim.email)
        self.assertEqual(role.user, victim)
        self.client.force_login(self.organizer)
        page = f"/events/{self.event.slug}/organize/judges/"
        r = self.client.post(page, {"link": role.pk}, follow=True)
        self.assertContains(r, "already has a working account")
        self.assertEqual(SignInLink.objects.filter(user=victim).count(), 0)
        r = self.client.post(
            f"/api/events/{self.event.slug}/judges/{victim.username}/sign-in-link", **self.bearer("organizer")
        )
        self.assertEqual(r.status_code, 400)
        # Once a judge has signed in, the organizer can no longer mint links for them either.
        invited = self.invite("second@example.org")
        invited.user.last_login = timezone.now()
        invited.user.save()
        with self.assertRaises(ValidationError):
            services.issue_judge_link(invited, self.organizer)

    def test_only_organizers_of_the_event_create_links(self):
        role = self.invite()
        for who in ("judge_a", "participant"):
            r = self.client.post(
                f"/api/events/{self.event.slug}/judges/{role.user.username}/sign-in-link", **self.bearer(who)
            )
            self.assertEqual(r.status_code, 403, who)
        self.assertEqual(SignInLink.objects.count(), 0)

    def test_link_is_shown_once_in_the_console(self):
        role = self.invite()
        self.client.force_login(self.organizer)
        page = f"/events/{self.event.slug}/organize/judges/"
        r = self.client.post(page, {"link": role.pk}, follow=True)
        self.assertContains(r, "/accounts/claim/")
        self.assertNotContains(self.client.get(page), "/accounts/claim/")
        entry = self.event.audit_entries.filter(action="judge.sign_in_link").first()
        self.assertNotIn("claim", str(entry.detail))


class AccountBehaviourTests(SeededTestCase):
    def test_next_is_only_followed_on_this_site(self):
        r = self.client.post(
            "/accounts/login/?next=https://evil.example.org/", {"username": "organizer", "password": "plumbline"}
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(r["Location"].startswith("https://evil"))
        c = Client()
        r = c.post(
            "/accounts/signup/?next=//evil.example.org/x",
            {
                "username": "fresh",
                "email": "fresh@example.org",
                "password1": "a-Long-passphrase-9",
                "password2": "a-Long-passphrase-9",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/dashboard/")
        c = Client()
        r = c.post(
            f"/accounts/signup/?next=/events/{self.event.slug}/",
            {
                "username": "fresh2",
                "email": "fresh2@example.org",
                "password1": "a-Long-passphrase-9",
                "password2": "a-Long-passphrase-9",
            },
        )
        self.assertEqual(r["Location"], f"/events/{self.event.slug}/")

    def test_email_is_unique_whatever_the_case(self):
        r = self.client.post(
            "/accounts/signup/",
            {
                "username": "dupe",
                "email": "ORGANIZER@example.org",
                "password1": "a-Long-passphrase-9",
                "password2": "a-Long-passphrase-9",
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="dupe").exists())

    def test_password_pages(self):
        self.assertEqual(self.client.get("/accounts/password_reset/").status_code, 200)
        self.client.force_login(self.participant)
        self.assertEqual(self.client.get("/accounts/password/").status_code, 200)
        r = self.client.post(
            "/accounts/password/",
            {
                "old_password": "plumbline",
                "new_password1": "a-Long-passphrase-9",
                "new_password2": "a-Long-passphrase-9",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)  # still signed in


class ErrorPageTests(SeededTestCase):
    def test_error_pages_use_the_site_layout(self):
        r = self.client.get("/no-such-page/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "Nothing at this address", status_code=404)
        self.assertContains(r, "plumbline.css", status_code=404)
        self.client.force_login(self.participant)
        r = self.client.get(f"/events/{self.event.slug}/organize/")
        self.assertContains(r, "Organizer role required", status_code=403)
        self.assertContains(r, "plumbline.css", status_code=403)

    def test_csrf_failure_page(self):
        c = Client(enforce_csrf_checks=True)
        r = c.post("/accounts/login/", {"username": "organizer", "password": "plumbline"})
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "That form expired", status_code=403)


class RoleChangeTests(SeededTestCase):
    def test_removing_a_judge_needs_no_reviews(self):
        role = EventRole.objects.get(event=self.event, role=Role.JUDGE, external_id="jdg_02")
        self.client.force_login(self.organizer)
        r = self.client.post(f"/events/{self.event.slug}/organize/judges/", {"remove": role.pk}, follow=True)
        self.assertContains(r, "submitted reviews")
        self.assertTrue(EventRole.objects.filter(pk=role.pk).exists())
