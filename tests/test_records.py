"""Signed records: what is issued, to whom, and what a check can tell."""

import copy
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings

from events import services as event_services
from events.models import TeamMembership
from judging import services as judging
from judging.models import JudgeAssignment, ProjectResult
from records import services
from records.models import Record

from .base import SeededTestCase


class Issued(SeededTestCase):
    def setUp(self):
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        self.out = services.issue_for_event(self.event, self.organizer, places=3)


class IssueTests(SeededTestCase):
    def test_not_before_publication_and_not_by_anyone_else(self):
        with self.assertRaises(ValidationError):
            services.issue_for_event(self.event, self.organizer)
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        for user in (self.judge_a, self.participant):
            with self.assertRaises(PermissionDenied):
                services.issue_for_event(self.event, user)
        self.assertEqual(Record.objects.count(), 0)


class IssuedTests(Issued):
    def test_everyone_owed_a_record_has_one(self):
        ranked = ProjectResult.objects.filter(event=self.event, rank__isnull=False)
        members = TeamMembership.objects.filter(team__projects__in=[r.project for r in ranked]).distinct().count()
        judges = (
            JudgeAssignment.objects.filter(event=self.event, status="submitted").values("judge_id").distinct().count()
        )
        self.assertEqual(self.out["judges"], judges)
        self.assertEqual(Record.objects.filter(kind="judge").count(), judges)
        self.assertEqual(Record.objects.exclude(kind="judge").count(), members)
        self.assertEqual(self.out["issued"], members + judges)
        top = Record.objects.filter(kind="placement")
        self.assertEqual({r.payload["place"] for r in top}, {1, 2, 3})
        self.assertTrue(all("place" not in r.payload for r in Record.objects.filter(kind="participation")))

    def test_running_it_again_issues_nothing(self):
        before = set(Record.objects.values_list("serial", flat=True))
        again = services.issue_for_event(self.event, self.organizer, places=3)
        self.assertEqual(again["issued"], 0)
        self.assertEqual(again["standing"], len(before))
        self.assertEqual(set(Record.objects.values_list("serial", flat=True)), before)

    def test_a_changed_place_replaces_the_record(self):
        first = ProjectResult.objects.get(event=self.event, rank=1)
        old = list(Record.objects.filter(project=first.project))
        self.assertTrue(old)
        event_services.set_hidden(first.project, self.organizer, True)
        judging.recompute_results(self.event, self.organizer)
        services.issue_for_event(self.event, self.organizer, places=3)
        # The hidden project's records are withdrawn; the new leader's say place 1.
        new_first = judging.placed(self.event)[0]
        standing = Record.objects.filter(project=new_first.project, revoked_at__isnull=True)
        self.assertEqual({r.payload["place"] for r in standing}, {1})
        replaced = Record.objects.filter(project=new_first.project, revoked_at__isnull=False)
        self.assertTrue(replaced.exists())
        self.assertEqual({r.revoke_reason for r in replaced}, {"replaced by a newer record"})
        for r in standing:
            self.assertEqual(
                Record.objects.filter(recipient=r.recipient, project=r.project, revoked_at__isnull=True).count(), 1
            )

    def test_a_judges_record_says_nothing_about_marks(self):
        rec = Record.objects.filter(kind="judge").first()
        self.assertEqual(
            set(rec.payload),
            {"serial", "kind", "event", "recipient", "issued_at", "issuer", "signed_with", "key", "reviews"},
        )
        page = self.client.get(rec.get_absolute_url()).content.decode()
        self.assertNotIn("Score", page)
        self.assertIn("served as a judge", page)


class CheckTests(Issued):
    def setUp(self):
        super().setUp()
        self.rec = Record.objects.filter(kind="placement").first()
        self.doc = services.document(self.rec)

    def test_genuine(self):
        out = services.check(self.doc["payload"], self.doc["signature"])
        self.assertEqual(out["state"], "genuine")
        self.assertEqual(out["record"], self.rec)

    def test_any_change_is_caught(self):
        for key, value in (
            ("place", 1 if self.rec.payload["place"] != 1 else 2),
            ("recipient", "Someone Else"),
            ("score", "5.000 / 5.00"),
        ):
            doc = copy.deepcopy(self.doc)
            doc["payload"][key] = value
            self.assertEqual(services.check(doc["payload"], doc["signature"])["state"], "altered", key)
        doc = copy.deepcopy(self.doc)
        doc["payload"]["extra"] = True
        self.assertEqual(services.check(doc["payload"], doc["signature"])["state"], "altered")
        self.assertEqual(services.check(self.doc["payload"], "0" * 128)["state"], "altered")

    def test_a_document_re_signed_with_another_key_is_caught(self):
        with override_settings(SECRET_KEY="someone-elses-secret"):
            forged = copy.deepcopy(self.doc["payload"])
            forged["place"] = 1
            signature = services.sign(forged)
        self.assertEqual(services.check(forged, signature)["state"], "altered")

    def test_a_well_signed_document_that_was_never_issued_is_unknown(self):
        payload = copy.deepcopy(self.doc["payload"])
        payload["serial"] = "PL-AAAA-BBBB-CCCC"
        self.assertEqual(services.check(payload, services.sign(payload))["state"], "unknown")

    def test_garbage_is_unknown_not_a_crash(self):
        for payload, signature in (
            (None, None),
            ("x", "y"),
            ([], ""),
            ({}, ""),
            ({"serial": 5}, "z"),
            ({"serial": None}, 3),
        ):
            self.assertEqual(services.check(payload, signature)["state"], "unknown")

    def test_withdrawn(self):
        with self.assertRaises(ValidationError):
            services.revoke(self.rec, self.organizer, "  ")
        with self.assertRaises(PermissionDenied):
            services.revoke(self.rec, self.judge_a, "because")
        services.revoke(self.rec, self.organizer, "issued to the wrong person")
        out = services.check(self.doc["payload"], self.doc["signature"])
        self.assertEqual(out["state"], "revoked")
        self.assertIn("issued to the wrong person", out["says"])
        with self.assertRaises(ValidationError):
            services.revoke(self.rec, self.organizer, "again")
        self.assertTrue(self.event.audit_entries.filter(action="records.revoke").exists())

    def test_the_key_is_not_the_secret_key_itself(self):
        import hashlib
        import hmac

        from django.conf import settings

        naive = hmac.new(settings.SECRET_KEY.encode(), services.canonical(self.rec.payload), hashlib.sha256)
        self.assertNotEqual(naive.hexdigest(), self.rec.signature)


class PagesTests(Issued):
    def setUp(self):
        super().setUp()
        self.rec = Record.objects.filter(kind="placement").first()

    def test_certificate_and_json(self):
        page = self.client.get(self.rec.get_absolute_url())
        self.assertContains(page, self.rec.payload["recipient"])
        self.assertContains(page, self.rec.serial)
        self.assertContains(page, "Genuine")
        self.assertContains(page, self.rec.signature)
        self.assertEqual(self.client.get(f"/records/{self.rec.serial.lower()}/").status_code, 200)
        doc = json.loads(self.client.get(f"/records/{self.rec.serial}.json").content)
        self.assertEqual(services.check(doc["payload"], doc["signature"])["state"], "genuine")
        self.assertEqual(self.client.get("/records/PL-NOPE-NOPE-NOPE/").status_code, 404)

    def test_verify_page(self):
        self.assertEqual(self.client.get("/verify/").status_code, 200)
        r = self.client.post("/verify/", {"document": self.rec.serial})
        self.assertContains(r, "Genuine")
        doc = services.document(self.rec)
        doc["payload"]["place"] = 99
        r = self.client.post("/verify/", {"document": json.dumps(doc)})
        self.assertContains(r, "Altered")
        self.assertNotContains(r, "Open the record")
        for junk in ("{", "{}", '{"payload": 3}', "PL-0000", "<script>"):
            r = self.client.post("/verify/", {"document": junk})
            self.assertEqual(r.status_code, 200, junk)
            self.assertContains(r, "Not found")

    def test_console(self):
        page = f"/events/{self.event.slug}/organize/records/"
        self.assertEqual(self.client.get(page).status_code, 302)
        for user in (self.judge_a, self.participant):
            self.client.force_login(user)
            self.assertEqual(self.client.get(page).status_code, 403)
            r = self.client.post(f"{page}{self.rec.serial}/revoke/", {"reason": "x"})
            self.assertEqual(r.status_code, 403)
        self.client.force_login(self.organizer)
        r = self.client.get(page)
        self.assertContains(r, self.rec.serial)
        r = self.client.post(page, {"action": "issue", "places": "abc"}, follow=True)
        self.assertContains(r, "must be a whole number")
        r = self.client.post(f"{page}{self.rec.serial}/revoke/", {"reason": "wrong name"}, follow=True)
        self.assertContains(r, "withdrawn")
        self.assertContains(self.client.get(self.rec.get_absolute_url()), "Withdrawn")

    def test_holders_see_their_own_on_the_dashboard(self):
        self.client.force_login(self.participant)
        mine = Record.objects.filter(recipient=self.participant, revoked_at__isnull=True)
        page = self.client.get("/dashboard/")
        for r in mine:
            self.assertContains(page, r.serial)
        other = Record.objects.exclude(recipient=self.participant).first()
        self.assertNotContains(page, other.serial)

    def test_api(self):
        r = self.client.post("/api/records/check", data=services.document(self.rec), content_type="application/json")
        self.assertEqual((r.status_code, r.json()["state"]), (200, "genuine"))
        r = self.client.get(f"/api/records/{self.rec.serial}")
        self.assertEqual(r.json()["signature"], self.rec.signature)
        r = self.client.get("/api/records/mine", **self.bearer("participant"))
        self.assertEqual(
            {x["serial"] for x in r.json()},
            set(Record.objects.filter(recipient=self.participant).values_list("serial", flat=True)),
        )
        self.assertEqual(
            self.client.get(f"/api/events/{self.event.slug}/records", **self.bearer("judge_a")).status_code, 403
        )
        self.assertEqual(
            len(self.client.get(f"/api/events/{self.event.slug}/records", **self.bearer("organizer")).json()),
            Record.objects.count(),
        )
        r = self.client.post(
            f"/api/events/{self.event.slug}/records/issue",
            data={"places": 3},
            content_type="application/json",
            **self.bearer("participant"),
        )
        self.assertEqual(r.status_code, 403)
        r = self.client.post(
            f"/api/events/{self.event.slug}/records/{self.rec.serial}/revoke",
            data={"reason": "test"},
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual((r.status_code, r.json()["state"]), (200, "revoked"))
