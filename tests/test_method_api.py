"""What the rubric page can change about the method, the API can change too."""

import json

from audit.models import AuditLog
from judging.services import ensure_rubric

from .base import SeededTestCase


class MethodApiTests(SeededTestCase):
    def setUp(self):
        self.url = f"/api/events/{self.event.slug}/judging/method"

    def patch(self, who, **body):
        return self.client.patch(self.url, json.dumps(body), content_type="application/json", **self.bearer(who))

    def test_an_organizer_reads_the_method(self):
        r = self.client.get(self.url, **self.bearer("organizer"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {"pairwise", "jury_size", "jury_size_in_use"})
        self.assertIs(r.json()["pairwise"], False)

    def test_nobody_else_reads_or_changes_it(self):
        for who in ("judge_a", "participant"):
            self.assertEqual(self.client.get(self.url, **self.bearer(who)).status_code, 403)
            self.assertEqual(self.patch(who, pairwise=True).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertIs(ensure_rubric(self.event).pairwise, False)

    def test_pairwise_is_switched_on_and_off_and_logged(self):
        r = self.patch("organizer", pairwise=True)
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["pairwise"], True)
        self.assertEqual(
            self.client.get(f"/api/events/{self.event.slug}/pairs/next", **self.bearer("judge_b")).status_code, 200
        )
        self.assertIs(self.patch("organizer", pairwise=False).json()["pairwise"], False)
        self.assertEqual(
            self.client.get(f"/api/events/{self.event.slug}/pairs/next", **self.bearer("judge_b")).status_code, 403
        )
        self.assertEqual(AuditLog.objects.filter(event=self.event, action="rubric.pairwise").count(), 2)

    def test_only_what_is_sent_changes(self):
        self.patch("organizer", pairwise=True, jury_size=5)
        r = self.patch("organizer", jury_size=0)
        self.assertEqual((r.json()["pairwise"], r.json()["jury_size"]), (True, 0))
        r = self.patch("organizer")
        self.assertEqual((r.json()["pairwise"], r.json()["jury_size"]), (True, 0))
        r = self.patch("organizer", jury_size_follows_event=True)
        self.assertIsNone(r.json()["jury_size"])

    def test_a_jury_size_out_of_range_changes_nothing(self):
        self.patch("organizer", jury_size=4)
        for bad in (-1, 101, 10**30):
            r = self.patch("organizer", jury_size=bad, pairwise=True)
            self.assertEqual(r.status_code, 400, bad)
        rubric = ensure_rubric(self.event)
        self.assertEqual((rubric.jury_k, rubric.pairwise), (4, False))
