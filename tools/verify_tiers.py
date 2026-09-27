#!/usr/bin/env python3
"""Probes for T3 and T4, in the manner of the official checker.

The official `checker/run.py` verifies T1 and T2. It has no probes for the
two tiers above them, so a claim of T3 or T4 would rest on the README. This
script is our answer: it makes requests to a running portal and prints what
it found, tier by tier. Standard library only. It reads the same
`.dogfood.toml` and uses the same four headers.

    python tools/verify_tiers.py .dogfood.toml > acceptance-report-t3-t4.txt

It needs somewhere to work, so it creates one event, named tier-probe-<time>,
unlisted, with seven small projects. Nothing else in the portal is changed.
Exit status is 0 when every probe passed.
"""

from __future__ import annotations

import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


class Portal:
    def __init__(self, base: str, headers: dict[str, str]):
        self.base = base.rstrip("/")
        self.headers = headers

    def call(self, method: str, path: str, who: str | None = None, body=None) -> tuple[int, object, dict]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if who:
            name, _, value = self.headers[who].partition(":")
            headers[name.strip()] = value.strip()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, raw, got = r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            status, raw, got = e.code, e.read(), dict(e.headers)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            parsed = raw.decode("utf-8", "replace")
        return status, parsed, got


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, bool, str]] = []

    def check(self, tier: str, label: str, ok: bool, detail: str = ""):
        self.rows.append((tier, label, bool(ok), detail))
        return bool(ok)

    def print(self, base: str):
        print("Plumbline probes for T3 and T4")
        print(f"portal: {base}")
        print()
        for tier, label, ok, detail in self.rows:
            dots = "." * max(3, 46 - len(label))
            print(f"{tier}  {label} {dots} {'PASS' if ok else 'FAIL'}")
            if not ok and detail:
                print(f"       {detail}")
        print()
        for tier in ("T3", "T4"):
            mine = [r for r in self.rows if r[0] == tier]
            passed = sum(1 for r in mine if r[2])
            print(f"{tier}: {passed} of {len(mine)} probes passed")
        return all(r[2] for r in self.rows)


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cfg = tomllib.loads(Path(argv[1]).read_text(encoding="utf-8"))
    portal = Portal(cfg["portal"]["base_url"], cfg["auth"])
    rep = Report()
    now = datetime.now(UTC)
    slug = f"tier-probe-{int(time.time())}"

    # ---- a place to work -----------------------------------------------------
    status, event, _ = portal.call(
        "POST",
        "/api/events",
        "organizer",
        {
            "name": f"Tier probe {slug[-6:]}",
            "slug": slug,
            "submissions_open_at": iso(now - timedelta(hours=2)),
            "submissions_close_at": iso(now + timedelta(hours=2)),
            "judging_open_at": iso(now - timedelta(hours=1)),
            "tracks": ["Alpha", "Beta"],
        },
    )
    if status != 201:
        print(f"could not create the probe event: {status} {event}")
        return 1
    e = f"/api/events/{slug}"
    portal.call("PATCH", e, "organizer", {"is_listed": False})

    rows = ["team,title,tagline,track,repo_url,tags,members"]
    for i in range(1, 8):
        rows.append(
            f"Team {i},Probe {i},Project {i} of the probe,{'Alpha' if i % 2 else 'Beta'},https://example.org/p{i},probe,"
        )
    csv_text = "\n".join(rows) + "\n"

    # ---- T4: bulk import, dry run first ---------------------------------------
    status, dry, _ = portal.call("POST", f"{e}/import", "organizer", {"kind": "projects", "csv": csv_text})
    status2, listed, _ = portal.call("GET", f"{e}/projects", "organizer")
    rep.check(
        "T4",
        "import: dry run reads and writes nothing",
        status == 200 and dry["ready"] and dry["total"] == 7 and listed == [],
        f"{status} {dry}",
    )
    bad = csv_text + "Team 9,Broken,,No such track,javascript:alert(1),,not-an-email\n"
    status, refused, _ = portal.call(
        "POST", f"{e}/import", "organizer", {"kind": "projects", "csv": bad, "dry_run": False}
    )
    status2, listed, _ = portal.call("GET", f"{e}/projects", "organizer")
    rep.check("T4", "import: one bad row stops the whole file", status == 400 and listed == [], f"{status} {refused}")
    status, done, _ = portal.call(
        "POST", f"{e}/import", "organizer", {"kind": "projects", "csv": csv_text, "dry_run": False}
    )
    status2, projects, _ = portal.call("GET", f"{e}/projects")
    ok = status == 200 and isinstance(projects, list) and len(projects) == 7
    rep.check("T4", "import: seven projects arrive, public", ok, f"{status} {done}")
    if not ok:
        rep.print(portal.base)
        return 1
    pid = projects[0]["id"]
    status, _, _ = portal.call("POST", f"{e}/import", "participant", {"kind": "projects", "csv": csv_text})
    rep.check("T4", "import: refused to a participant", status == 403, str(status))

    # ---- T3: voting ------------------------------------------------------------
    status, _, _ = portal.call("GET", f"{e}/ballot", "participant")
    rep.check("T3", "no ballot while voting is closed", status == 403, str(status))
    portal.call("PATCH", e, "organizer", {"voting_access": "auth", "voting_credits": 9})
    status, ballot, _ = portal.call("GET", f"{e}/ballot", "participant")
    ok = status == 200 and len(ballot["items"]) == 7 and ballot["credits"] == 9
    rep.check("T3", "signed-in voter gets a ballot", ok, f"{status} {ballot}")
    rep.check(
        "T3",
        "the ballot shows no totals",
        ok and all(set(i) <= {"project_id", "title", "tagline", "track", "team", "my_weight"} for i in ballot["items"]),
    )
    status_b, ballot_b, _ = portal.call("GET", f"{e}/ballot", "judge_b")
    status_a, again, _ = portal.call("GET", f"{e}/ballot", "participant")
    order = [i["project_id"] for i in ballot["items"]]
    rep.check(
        "T3",
        "ballot order differs between voters",
        status_b == 200 and order != [i["project_id"] for i in ballot_b["items"]],
        "two voters were shown the same order",
    )
    rep.check("T3", "ballot order is stable for one voter", order == [i["project_id"] for i in again["items"]])
    status, _, _ = portal.call("POST", f"{e}/projects/{pid}/vote", "participant", {"weight": 2})
    rep.check("T3", "a vote is accepted", status == 200, str(status))
    status, over, _ = portal.call("POST", f"{e}/projects/{projects[1]['id']}/vote", "participant", {"weight": 3})
    rep.check("T3", "the quadratic budget holds (4 + 9 > 9)", status == 400, f"{status} {over}")
    status, used, _ = portal.call("GET", f"{e}/ballot", "participant")
    rep.check("T3", "the ballot remembers the vote", status == 200 and used["credits_used"] == 4, f"{status}")
    status, _, _ = portal.call("POST", f"{e}/projects/{pid}/vote", None, {"weight": 1})
    rep.check("T3", "no vote without a voter", status in (401, 403), str(status))
    status, summary, _ = portal.call("GET", f"{e}/votes/summary", "participant")
    rep.check("T3", "the tally is closed to voters", status == 403, str(status))
    status, summary, _ = portal.call("GET", f"{e}/votes/summary", "organizer")
    rep.check(
        "T3",
        "the organizer sees the tally and the flags",
        status == 200 and summary["ballots"] == 2,
        f"{status} {summary}",
    )

    # ---- T3: comments ----------------------------------------------------------
    text = f"Probe comment {slug[-6:]}"
    status, _, _ = portal.call("POST", f"{e}/projects/{pid}/comments", "participant", {"body": text})
    rep.check("T3", "a signed-in visitor can comment", status == 201, str(status))
    status, _, _ = portal.call("POST", f"{e}/projects/{pid}/comments", "participant", {"body": text})
    rep.check("T3", "the same comment twice is refused", status == 400, str(status))
    status, _, _ = portal.call("POST", f"{e}/projects/{pid}/comments", None, {"body": "anonymous"})
    rep.check("T3", "no comment without an account", status in (401, 403), str(status))
    status, comments, _ = portal.call("GET", f"{e}/projects/{pid}/comments")
    cid = comments[0]["id"] if status == 200 and comments else 0
    rep.check("T3", "comments are public", status == 200 and any(c["body"] == text for c in comments), str(status))
    status, _, _ = portal.call("POST", f"{e}/projects/{pid}/comments/{cid}/hide", "participant")
    rep.check("T3", "only organizers moderate", status == 403, str(status))
    portal.call("POST", f"{e}/projects/{pid}/comments/{cid}/hide", "organizer")
    status, comments, _ = portal.call("GET", f"{e}/projects/{pid}/comments")
    rep.check("T3", "a hidden comment leaves the public list", status == 200 and not comments, f"{status} {comments}")

    # ---- T3: results hidden, audit trail ---------------------------------------
    judge = portal.call("GET", "/api/judges/me/scores", "judge_a")[1]["judge"]
    portal.call("POST", f"{e}/judges", "organizer", {"email": f"{judge}@example.org"})
    status, a, _ = portal.call("POST", f"{e}/assignments", "organizer", {"judge": judge, "project_id": pid})
    aid = a["id"] if status == 201 else 0
    status, _, _ = portal.call(
        "POST",
        f"/api/judges/me/assignments/{aid}/scores",
        "judge_a",
        {"scores": {"functionality": 4, "quality": 5, "innovation": 3}, "comment": "probe", "submit": True},
    )
    scored = status == 200
    portal.call("POST", f"{e}/results/recompute", "organizer")
    status_p, _, _ = portal.call("GET", f"{e}/results", "participant")
    status_v, _, _ = portal.call("GET", f"{e}/results")
    rep.check(
        "T3",
        "results are hidden before publication",
        scored and status_p == 403 and status_v == 403,
        f"{status_p} {status_v}",
    )
    status, audit, _ = portal.call("GET", f"{e}/audit?action=vote", "organizer")
    rep.check(
        "T3",
        "votes are in the audit trail",
        status == 200 and any(x["action"] == "vote.cast" for x in audit),
        str(status),
    )
    status, _, _ = portal.call("GET", f"{e}/audit", "participant")
    rep.check("T3", "the audit trail is for organizers", status == 403, str(status))
    portal.call("POST", f"{e}/results/publish", "organizer")
    status, results, _ = portal.call("GET", f"{e}/results")
    ok = status == 200 and results and results[0]["adjusted_mean"] is not None
    rep.check("T3", "results are public once published", ok, str(status))

    # ---- T4: API ---------------------------------------------------------------
    status, spec, _ = portal.call("GET", "/api/openapi.json")
    ok = status == 200 and isinstance(spec, dict) and len(spec.get("paths", {})) >= 40
    rep.check(
        "T4",
        "OpenAPI document is served",
        ok,
        f"{status}, {len(spec.get('paths', {})) if isinstance(spec, dict) else 0} paths",
    )
    status, docs, _ = portal.call("GET", "/api/docs")
    rep.check(
        "T4",
        "API reference needs no network",
        status == 200 and "http://" not in str(docs).replace(portal.base, "") and "https://" not in str(docs),
        str(status),
    )

    # ---- T4: webhooks ----------------------------------------------------------
    status, hook, _ = portal.call(
        "POST", f"{e}/webhooks", "organizer", {"url": "https://receiver.invalid/hook", "actions": ["project."]}
    )
    rep.check(
        "T4", "webhook: created, secret shown once", status == 201 and len(hook.get("secret", "")) >= 32, str(status)
    )
    status, hooks, _ = portal.call("GET", f"{e}/webhooks", "organizer")
    rep.check(
        "T4",
        "webhook: secret is masked afterwards",
        status == 200 and hooks and len(hooks[0]["secret"]) < 12,
        str(status),
    )
    status, _, _ = portal.call("POST", f"{e}/webhooks", "organizer", {"url": "http://127.0.0.1:5432/"})
    rep.check("T4", "webhook: an internal address is refused", status == 400, str(status))
    status, delivery, _ = portal.call("POST", f"{e}/webhooks/{hook.get('id', 0)}/ping", "organizer")
    ok = status == 200 and delivery["attempts"] == 1 and delivery["status"] in ("ok", "failed")
    rep.check("T4", "webhook: a delivery is recorded with its outcome", ok, f"{status} {delivery}")
    status, _, _ = portal.call("GET", f"{e}/webhooks", "judge_a")
    rep.check("T4", "webhook: closed to judges", status == 403, str(status))

    # ---- T4: records -----------------------------------------------------------
    status, issued, _ = portal.call("POST", f"{e}/records/issue", "organizer", {"places": 1})
    rep.check("T4", "records: issued after publication", status == 200 and issued["judges"] == 1, f"{status} {issued}")
    status, again, _ = portal.call("POST", f"{e}/records/issue", "organizer", {"places": 1})
    rep.check(
        "T4", "records: issuing twice issues nothing new", status == 200 and again["issued"] == 0, f"{status} {again}"
    )
    status, mine, _ = portal.call("GET", "/api/records/mine", "judge_a")
    record = next((r for r in mine if r["payload"]["event"]["slug"] == slug), None) if status == 200 else None
    rep.check(
        "T4", "records: the judge holds a record of judging", bool(record) and record["kind"] == "judge", str(status)
    )
    if record:
        rep.check(
            "T4",
            "records: it says nothing of the marks given",
            not ({"score", "scores", "place"} & set(record["payload"])),
        )
        status, public, _ = portal.call("GET", f"/api/records/{record['serial']}")
        rep.check(
            "T4",
            "records: readable by anyone with the number",
            status == 200 and public["state"] == "genuine",
            str(status),
        )
        doc = {"payload": record["payload"], "signature": record["signature"]}
        status, verdict, _ = portal.call("POST", "/api/records/check", None, doc)
        rep.check(
            "T4",
            "records: a genuine record checks as genuine",
            status == 200 and verdict["state"] == "genuine",
            f"{status} {verdict}",
        )
        forged = json.loads(json.dumps(doc))
        forged["payload"]["reviews"] = 99
        status, verdict, _ = portal.call("POST", "/api/records/check", None, forged)
        rep.check(
            "T4",
            "records: one changed value is caught",
            status == 200 and verdict["state"] == "altered",
            f"{status} {verdict}",
        )
        status, _, _ = portal.call("POST", f"{e}/records/{record['serial']}/revoke", "participant", {"reason": "x"})
        rep.check("T4", "records: only organizers withdraw", status == 403, str(status))
        portal.call("POST", f"{e}/records/{record['serial']}/revoke", "organizer", {"reason": "probe finished"})
        status, verdict, _ = portal.call("POST", "/api/records/check", None, doc)
        rep.check(
            "T4",
            "records: a withdrawn record says so",
            status == 200 and verdict["state"] == "revoked",
            f"{status} {verdict}",
        )
        status, page, _ = portal.call("GET", f"/records/{record['serial']}/")
        rep.check("T4", "certificate page is served", status == 200 and record["serial"] in str(page), str(status))

    # ---- T4: embed and export --------------------------------------------------
    status, page, got = portal.call("GET", f"/events/{slug}/embed/gallery/?limit=3")
    framed = "X-Frame-Options" not in {k.title() for k in got}
    rep.check(
        "T4", "embed: the gallery may sit in a frame", status == 200 and framed and "Probe" in str(page), str(status)
    )
    status, _, got = portal.call("GET", f"/events/{slug}/gallery/")
    rep.check("T4", "embed: no other page may", {k.title(): v for k, v in got.items()}.get("X-Frame-Options") == "DENY")
    for kind in ("projects", "assignments", "scores", "results", "calibration", "audit"):
        status, text, _ = portal.call("GET", f"{e}/export/{kind}.csv", "organizer")
        rep.check("T4", f"export: {kind}.csv", status == 200 and "," in str(text).splitlines()[0], str(status))
    status, _, _ = portal.call("GET", f"{e}/export/scores.csv", "participant")
    rep.check("T4", "export: refused to a participant", status == 403, str(status))

    return 0 if rep.print(portal.base) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
