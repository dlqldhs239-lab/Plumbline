"""Records are signed so that anyone can check them, with or without the portal."""

import copy
import importlib.util
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import override_settings

from judging import services as judging
from judging.models import ProjectResult
from records import services, signing
from records.models import Record

from .base import SeededTestCase

TOOL = Path(__file__).resolve().parent.parent / "tools" / "verify_record.py"
spec = importlib.util.spec_from_file_location("verify_record", TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

# RFC 8032, section 7.1, test 2: a one-byte message.
RFC_PUBLIC = "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"
RFC_MESSAGE = "72"
RFC_SIGNATURE = (
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
    "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
)


class Issued(SeededTestCase):
    def setUp(self):
        judging.recompute_results(self.event, self.organizer)
        judging.publish_results(self.event, self.organizer)
        services.issue_for_event(self.event, self.organizer, places=3)
        first = ProjectResult.objects.get(event=self.event, rank=1)
        self.rec = Record.objects.filter(project=first.project, kind="placement").first()
        self.doc = services.document(self.rec)

    def run_tool(self, document, *args):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "record.json"
            path.write_text(json.dumps(document) if not isinstance(document, str) else document, encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                code = tool.main([str(path), *args])
        return code, out.getvalue()


class StandardLibraryVerifierTests(SeededTestCase):
    def test_it_agrees_with_the_rfc(self):
        public, message, signature = (bytes.fromhex(x) for x in (RFC_PUBLIC, RFC_MESSAGE, RFC_SIGNATURE))
        self.assertTrue(tool.ed25519_fits(public, message, signature))
        self.assertFalse(tool.ed25519_fits(public, b"\x73", signature))
        self.assertFalse(tool.ed25519_fits(public, message, signature[:-1] + b"\x01"))

    def test_it_agrees_with_the_library_on_many_messages(self):
        for n in range(24):
            key = Ed25519PrivateKey.from_private_bytes(bytes([n]) * 32)
            from cryptography.hazmat.primitives import serialization

            public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            message = (f"message {n} " * n).encode()
            signature = key.sign(message)
            self.assertTrue(tool.ed25519_fits(public, message, signature), n)
            self.assertFalse(tool.ed25519_fits(public, message + b"!", signature), n)
            other = Ed25519PrivateKey.from_private_bytes(bytes([n + 100]) * 32).sign(message)
            self.assertFalse(tool.ed25519_fits(public, message, other), n)

    def test_what_is_not_a_key_or_a_signature_does_not_fit(self):
        good = bytes.fromhex(RFC_PUBLIC)
        for public, signature in (
            (b"", b""),
            (good, b"\x00" * 63),
            (good[:31], bytes.fromhex(RFC_SIGNATURE)),
            (b"\xff" * 32, bytes.fromhex(RFC_SIGNATURE)),
            (good, b"\xff" * 64),
        ):
            self.assertFalse(tool.ed25519_fits(public, b"r", signature))


class SignedRecordTests(Issued):
    def test_a_record_names_its_method_and_its_key(self):
        self.assertEqual(self.rec.payload["signed_with"], "Ed25519")
        self.assertEqual(self.rec.payload["key"], signing.fingerprint())
        self.assertEqual(len(self.rec.signature), 128)
        self.assertEqual(self.doc["public_key"], signing.public_key())

    def test_anyone_can_check_it_without_the_portal(self):
        code, said = self.run_tool(self.doc, "--key", signing.public_key())
        self.assertEqual(code, 0, said)
        self.assertIn("FITS", said)
        self.assertIn("not checked", said)

    def test_a_changed_record_does_not_fit(self):
        for key, value in (("place", 2), ("recipient", "Someone Else"), ("serial", "PL-AAAA-BBBB-CCCC")):
            doc = copy.deepcopy(self.doc)
            doc["payload"][key] = value
            code, said = self.run_tool(doc, "--key", signing.public_key())
            self.assertEqual(code, 1, key)
            self.assertIn("DOES NOT FIT", said)

    def test_a_record_signed_by_someone_else_does_not_fit_our_key(self):
        forger = Ed25519PrivateKey.generate()
        payload = copy.deepcopy(self.doc["payload"])
        payload["place"] = 1
        forged = {"payload": payload, "signature": forger.sign(signing.canonical(payload)).hex()}
        self.assertEqual(self.run_tool(forged, "--key", signing.public_key())[0], 1)
        self.assertEqual(services.check(forged["payload"], forged["signature"])["state"], "altered")

    def test_what_is_not_a_record_is_said_to_be_unreadable(self):
        for text in (
            "",
            "[]",
            "{}",
            '{"payload": 1, "signature": "x"}',
            '{"payload": {"a": 1, "a": 2}, "signature": "x"}',
        ):
            self.assertEqual(self.run_tool(text, "--key", signing.public_key())[0], 2, text)
        self.assertEqual(self.run_tool(self.doc, "--key", "not hex")[0], 2)

    def test_the_key_is_published(self):
        r = self.client.get("/verify/key.json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["public_key"], signing.public_key())
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")
        self.assertEqual(self.client.get("/api/records/key").json()["fingerprint"], signing.fingerprint())
        page = self.client.get("/verify/")
        self.assertContains(page, signing.public_key())
        self.assertContains(page, "Check it without us")
        self.assertNotIn("private", json.dumps(r.json()).lower())

    def test_the_portal_still_answers_for_itself(self):
        self.assertEqual(services.check(self.doc["payload"], self.doc["signature"])["state"], "genuine")
        self.assertEqual(services.check(self.doc["payload"], self.doc["signature"].upper())["state"], "genuine")
        self.assertEqual(services.check(self.doc["payload"], "0" * 128)["state"], "altered")
        self.assertEqual(services.check(self.doc["payload"], "zz" * 64)["state"], "altered")
        services.revoke(self.rec, self.organizer, "issued to the wrong person")
        self.assertEqual(services.check(self.doc["payload"], self.doc["signature"])["state"], "revoked")
        # The signature still fits: that a record was withdrawn is for the portal to say.
        self.assertEqual(self.run_tool(self.doc, "--key", signing.public_key())[0], 0)


class KeysTests(Issued):
    def test_records_signed_the_old_way_still_verify_here(self):
        payload = copy.deepcopy(self.rec.payload)
        payload["signed_with"] = "HMAC-SHA256"
        payload.pop("key")
        old = signing.sign_hmac(payload)
        Record.objects.filter(pk=self.rec.pk).update(payload=payload, signature=old)
        self.assertEqual(services.check(payload, old)["state"], "genuine")
        changed = dict(payload, place=2)
        self.assertEqual(services.check(changed, old)["state"], "altered")
        self.rec.refresh_from_db()
        self.assertNotIn("public_key", services.document(self.rec))
        self.assertEqual(self.run_tool(services.document(self.rec), "--key", signing.public_key())[0], 2)

    def test_a_method_nobody_knows_fits_nothing(self):
        payload = dict(self.rec.payload, signed_with="none")
        Record.objects.filter(pk=self.rec.pk).update(payload=payload)
        self.assertEqual(services.check(payload, "")["state"], "altered")
        self.assertEqual(services.check(payload, self.rec.signature)["state"], "altered")

    def test_changing_the_secret_breaks_the_records_unless_they_have_a_key_of_their_own(self):
        with override_settings(SECRET_KEY="a-new-secret"):
            self.assertEqual(services.check(self.doc["payload"], self.doc["signature"])["state"], "altered")
        with override_settings(PLUMBLINE_RECORDS_KEY="kept apart"):
            public = signing.public_key()
            self.assertNotEqual(public, self.doc["public_key"])
            Record.objects.all().delete()
            services.issue_for_event(self.event, self.organizer, places=3)
            rec = Record.objects.filter(kind="placement").first()
            with override_settings(SECRET_KEY="a-new-secret"):
                self.assertEqual(signing.public_key(), public)
                self.assertEqual(services.state_of(rec)["state"], "genuine")

    def test_the_key_is_the_same_from_one_start_to_the_next(self):
        self.assertEqual(signing.public_key(), signing.public_key())
        self.assertRegex(signing.fingerprint(), r"^[0-9a-f]{4}(-[0-9a-f]{4}){3}$")
