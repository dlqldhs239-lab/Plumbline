"""Signing records so that anyone can check them, with or without this portal.

A record is signed with Ed25519. The private key stays here; the public key
is printed in every record and published at /verify/key.json. Whoever holds
a record and the public key can check the signature on their own machine:
`tools/verify_record.py` does it with the Python standard library.

What a signature cannot say is whether the record still stands (it may have
been withdrawn, or the results may have changed). That question is for the
portal, at /verify/.

Records issued before this were signed with HMAC-SHA256, which only the
portal can check. They still verify here.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

ED25519 = "Ed25519"
HMAC_SHA256 = "HMAC-SHA256"


def canonical(payload: dict) -> bytes:
    """The bytes that are signed: the payload as JSON with sorted keys, no
    spaces, UTF-8. Written down because a verifier has to build the same."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _seed() -> bytes:
    """Thirty-two bytes from which the key is made. PLUMBLINE_RECORDS_KEY, if
    set, so that the installation's secret can be changed without every
    record issued so far failing its check; otherwise the secret itself,
    under a label of its own, so a signature here is never a session or a
    token anywhere else."""
    own = (getattr(settings, "PLUMBLINE_RECORDS_KEY", "") or "").strip()
    source = own or settings.SECRET_KEY
    return hashlib.sha256(("plumbline.records.ed25519|" + source).encode()).digest()


def _private() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(_seed())


def public_key() -> str:
    raw = _private().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw.hex()


def fingerprint(public_hex: str | None = None) -> str:
    """Short enough to read over the telephone."""
    digest = hashlib.sha256(bytes.fromhex(public_hex or public_key())).hexdigest()[:16]
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


def sign(payload: dict) -> str:
    return _private().sign(canonical(payload)).hex()


def _hmac_key() -> bytes:
    return hashlib.sha256(("plumbline.records|" + settings.SECRET_KEY).encode()).digest()


def sign_hmac(payload: dict) -> str:
    """How records were signed before. Kept to check the ones already issued."""
    return hmac.new(_hmac_key(), canonical(payload), hashlib.sha256).hexdigest()


def fits(payload: dict, signature: str) -> bool:
    """Does the signature fit the payload, by the method the payload names?"""
    try:
        text = signature.strip().lower()
        message = canonical(payload)
    except (TypeError, ValueError, RecursionError, UnicodeError, AttributeError):
        return False
    method = payload.get("signed_with") if isinstance(payload, dict) else None
    if method == HMAC_SHA256:
        expected = hmac.new(_hmac_key(), message, hashlib.sha256).hexdigest().encode("ascii")
        return hmac.compare_digest(expected, text.encode("utf-8", "replace"))
    if method != ED25519 or len(text) != 128:
        return False
    try:
        raw = bytes.fromhex(text)
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key())).verify(raw, message)
    except (ValueError, InvalidSignature):
        return False
    return True


def key_document() -> dict:
    """What /verify/key.json publishes."""
    return {
        "algorithm": ED25519,
        "public_key": public_key(),
        "encoding": "hex, 32 bytes",
        "fingerprint": fingerprint(),
        "signed_bytes": "the payload as JSON: keys sorted, separators ',' and ':', UTF-8, no escaping of non-ASCII",
        "signature_encoding": "hex, 64 bytes",
        "issuer": settings.PLUMBLINE_SITE_NAME,
        "note": (
            "A signature that fits says this portal issued the record and nothing in it was changed. "
            "Whether the record still stands is answered by the portal, at /verify/."
        ),
    }
