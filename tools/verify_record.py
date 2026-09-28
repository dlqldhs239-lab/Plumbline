"""Check the signature of a Plumbline record on your own machine.

    python tools/verify_record.py record.json --key <public key, hex>
    python tools/verify_record.py record.json --portal http://localhost:8080

Python 3 and its standard library, nothing to install, no portal needed for
the first form. The record is the file you get from "Signed record, JSON"
on a certificate. The public key is printed in that file; take it from
somewhere you trust instead (the organizer's website, /verify/key.json on
their portal), because a forger would print their own.

Exit code 0: the signature fits. 1: it does not. 2: the file could not be read.

A signature that fits says that the holder of the key issued this record
and that nothing in it was changed since. It does not say that the record
still stands: it may have been withdrawn. Ask the portal for that, which the
second form does as well.

The Ed25519 verification below follows RFC 8032, section 6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request

P = 2**255 - 19
D = -121665 * pow(121666, P - 2, P) % P
Q = 2**252 + 27742317777372353535851937790883648493
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    E = 2 * a[2] * b[2] % P
    e, f, g, h = B - A, E - C, E + C, B + A
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, point):
    out = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            out = _add(out, point)
        point = _add(point, point)
        s >>= 1
    return out


def _same(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


BASE_Y = 4 * pow(5, P - 2, P) % P
BASE_X = _x(BASE_Y, 0)
BASE = (BASE_X, BASE_Y, 1, BASE_X * BASE_Y % P)


def _point(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def ed25519_fits(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _point(public)
    r = _point(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % Q
    return _same(_mul(s, BASE), _add(r, _mul(h, a)))


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _no_repeats(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"the key {key!r} is written twice")
        out[key] = value
    return out


def _fetch(url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - the address is the user's own
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check the signature of a Plumbline record.")
    parser.add_argument("record", help="the JSON file of the record")
    parser.add_argument("--key", help="the issuer's public key, 64 hex characters")
    parser.add_argument(
        "--portal", help="the issuer's portal, to take the key from and to ask whether the record stands"
    )
    args = parser.parse_args(argv)

    try:
        with open(args.record, encoding="utf-8") as handle:
            document = json.loads(handle.read(), object_pairs_hook=_no_repeats)
        payload, signature = document["payload"], document["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise ValueError("payload must be an object and signature a string")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"cannot read the record: {error}")
        return 2

    if payload.get("signed_with") != "Ed25519":
        print(f"signed with {payload.get('signed_with')!r}: only the portal that issued it can check this one")
        return 2

    key, source = args.key, "given on the command line"
    portal = (args.portal or "").rstrip("/")
    if not key and portal:
        try:
            key, source = _fetch(portal + "/verify/key.json")["public_key"], f"published by {portal}"
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            print(f"cannot read the key from {portal}: {error}")
            return 2
    if not key:
        key, source = document.get("public_key"), "printed in the record itself, which a forger could also do"
    try:
        public = bytes.fromhex(str(key).strip())
        raw = bytes.fromhex(signature.strip())
    except ValueError:
        print("the key and the signature must be hex")
        return 2

    print(f"record      {payload.get('serial')}")
    print(
        f"says        {payload.get('recipient')} · {payload.get('kind')} · {(payload.get('event') or {}).get('name')}"
    )
    print(f"key         {key[:16]}… ({source})")
    good = ed25519_fits(public, canonical(payload), raw)
    print("signature   " + ("FITS: issued by the holder of this key, and unchanged" if good else "DOES NOT FIT"))
    if good and portal:
        try:
            answer = _fetch(portal + "/api/records/check", {"payload": payload, "signature": signature})
            print(f"portal      {answer.get('state')}: {answer.get('says')}")
        except (OSError, ValueError, urllib.error.URLError) as error:
            print(f"portal      could not be asked: {error}")
    elif good:
        print("standing    not checked. Ask the issuer's portal at /verify/ whether the record was withdrawn.")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
