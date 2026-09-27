"""Webhooks: subscription to the audit stream, signing, failure and retry.
The network is replaced by a recorder; nothing here leaves the process."""

import json
from unittest import mock

from django.core.management import call_command
from django.test import override_settings

from events.models import Project
from integrations import services
from integrations.models import Webhook, WebhookDelivery

from .base import SeededTestCase


class Receiver:
    """Stands in for the remote server."""

    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body, "headers": headers})
        if isinstance(self.status, Exception):
            raise self.status
        return self.status, "ok"


@override_settings(PLUMBLINE_WEBHOOKS_ASYNC=False)
class WebhookTests(SeededTestCase):
    def url(self, tail=""):
        return f"/api/events/{self.event.slug}/webhooks{tail}"

    def deliveries(self, tail=""):
        return f"/api/events/{self.event.slug}/webhook-deliveries{tail}"

    def make_hook(self, actions=None):
        return services.create_webhook(self.event, self.organizer, "https://receiver.example.org/hook", actions or [])

    def test_state_change_is_delivered_signed_after_commit(self):
        hook = self.make_hook()
        project = Project.objects.get(external_id="prj_01")
        receiver = Receiver()
        with mock.patch.object(services, "_post", receiver):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.client.post(
                    f"/api/events/{self.event.slug}/projects/{project.pk}/hide",
                    content_type="application/json",
                    **self.bearer("organizer"),
                )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(receiver.calls), 1)
        call = receiver.calls[0]
        body = json.loads(call["body"])
        self.assertEqual(body["action"], "project.hide")
        self.assertEqual(body["event"], self.event.slug)
        self.assertEqual(body["actor"], self.organizer.username)
        self.assertEqual(body["target"]["id"], str(project.pk))
        self.assertNotIn("ip", json.dumps(body))
        self.assertEqual(call["headers"]["X-Plumbline-Event"], "project.hide")
        self.assertTrue(services.verify(hook.secret, call["body"], call["headers"]["X-Plumbline-Signature"]))
        self.assertFalse(services.verify("wrong-secret", call["body"], call["headers"]["X-Plumbline-Signature"]))
        d = WebhookDelivery.objects.get()
        self.assertEqual((d.status, d.status_code, d.attempts), ("ok", 200, 1))

    def test_nothing_is_sent_if_the_change_is_refused(self):
        self.make_hook()
        receiver = Receiver()
        with mock.patch.object(services, "_post", receiver):
            with self.captureOnCommitCallbacks(execute=True):
                r = self.client.post(
                    f"/api/events/{self.event.slug}/projects/1/hide",
                    content_type="application/json",
                    **self.bearer("participant"),
                )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(receiver.calls, [])
        self.assertEqual(WebhookDelivery.objects.count(), 0)

    def test_action_filter_and_paused_hooks(self):
        scores_only = self.make_hook(["score."])
        paused = self.make_hook()
        services.set_active(paused, self.organizer, False)
        receiver = Receiver()
        with mock.patch.object(services, "_post", receiver):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    f"/api/events/{self.event.slug}/results/recompute",
                    content_type="application/json",
                    **self.bearer("organizer"),
                )
        self.assertEqual(receiver.calls, [])
        self.assertTrue(scores_only.wants("score.submit"))
        self.assertFalse(scores_only.wants("results.recompute"))
        self.assertFalse(paused.wants("score.submit"))

    def test_webhook_bookkeeping_is_audited_but_not_sent(self):
        receiver = Receiver()
        with mock.patch.object(services, "_post", receiver):
            with self.captureOnCommitCallbacks(execute=True):
                first = self.make_hook()
                self.make_hook()
                services.set_active(first, self.organizer, False)
        self.assertEqual(receiver.calls, [])
        actions = list(self.event.audit_entries.filter(action__startswith="webhook.").values_list("action", flat=True))
        self.assertEqual(sorted(actions), ["webhook.create", "webhook.create", "webhook.disable"])

    def test_failure_is_recorded_and_retry_succeeds(self):
        hook = self.make_hook()
        with mock.patch.object(services, "_post", Receiver(status=500)):
            d = services.send_test(hook, self.organizer)
        self.assertEqual((d.status, d.status_code, d.attempts), ("failed", 500, 1))
        with mock.patch.object(services, "_post", Receiver(status=TimeoutError("timed out"))):
            r = self.client.post(self.deliveries(f"/{d.pk}/retry"), **self.bearer("organizer"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "failed")
        self.assertIn("TimeoutError", r.json()["error"])
        with mock.patch.object(services, "_post", Receiver(status=204)):
            r = self.client.post(self.deliveries(f"/{d.pk}/retry"), **self.bearer("organizer"))
        self.assertEqual((r.json()["status"], r.json()["attempts"]), ("ok", 3))
        r = self.client.post(self.deliveries(f"/{d.pk}/retry"), **self.bearer("organizer"))
        self.assertEqual(r.status_code, 400)  # already delivered

    @override_settings(PLUMBLINE_WEBHOOK_MAX_ATTEMPTS=2)
    def test_retries_stop_at_the_limit_and_command_retries_the_rest(self):
        hook = self.make_hook()
        with mock.patch.object(services, "_post", Receiver(status=503)):
            d = services.send_test(hook, self.organizer)
            call_command("deliver_webhooks", stdout=mock.Mock())
            d.refresh_from_db()
            self.assertEqual(d.attempts, 2)
            call_command("deliver_webhooks", stdout=mock.Mock())
            d.refresh_from_db()
            self.assertEqual(d.attempts, 2)  # gave up
            r = self.client.post(self.deliveries(f"/{d.pk}/retry"), **self.bearer("organizer"))
            self.assertEqual(r.status_code, 400)

    def test_api_crud_and_permissions(self):
        r = self.client.post(
            self.url(),
            data={"url": "https://receiver.example.org/a", "actions": ["project."], "description": "CI"},
            content_type="application/json",
            **self.bearer("organizer"),
        )
        self.assertEqual(r.status_code, 201, r.content)
        hook_id = r.json()["id"]
        self.assertGreaterEqual(len(r.json()["secret"]), 32)
        r = self.client.post(
            self.url(), data={"url": "ftp://nope"}, content_type="application/json", **self.bearer("organizer")
        )
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            self.url(), data={"url": "javascript:alert(1)"}, content_type="application/json", **self.bearer("organizer")
        )
        self.assertEqual(r.status_code, 400)
        for who in ("judge_a", "participant"):
            self.assertEqual(self.client.get(self.url(), **self.bearer(who)).status_code, 403)
            self.assertEqual(self.client.get(self.deliveries(), **self.bearer(who)).status_code, 403)
            r = self.client.post(
                self.url(), data={"url": "https://x.example.org"}, content_type="application/json", **self.bearer(who)
            )
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.client.delete(self.url(f"/{hook_id}"), **self.bearer(who)).status_code, 403)
        self.assertEqual(len(self.client.get(self.url(), **self.bearer("organizer")).json()), 1)
        r = self.client.post(self.url(f"/{hook_id}/active?active=false"), **self.bearer("organizer"))
        self.assertFalse(r.json()["active"])
        self.assertEqual(self.client.delete(self.url(f"/{hook_id}"), **self.bearer("organizer")).status_code, 204)
        self.assertEqual(Webhook.objects.count(), 0)

    def test_console_page(self):
        self.client.force_login(self.organizer)
        page = f"/events/{self.event.slug}/organize/integrations/"
        self.assertEqual(self.client.get(page).status_code, 200)
        r = self.client.post(
            page, {"action": "create", "url": "https://receiver.example.org/ui", "actions": "score., results."}
        )
        self.assertEqual(r.status_code, 302)
        hook = Webhook.objects.get()
        self.assertEqual(hook.actions, ["score.", "results."])
        with mock.patch.object(services, "_post", Receiver()):
            self.client.post(page, {"action": "test", "webhook": hook.pk})
        self.assertContains(self.client.get(page), "webhook.ping")
        self.client.force_login(self.judge_a)
        self.assertEqual(self.client.get(page).status_code, 403)
