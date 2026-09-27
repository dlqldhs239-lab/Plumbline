"""Every drawing that is wiped in must arrive once it has been scrolled to, at every width.

Needs a running portal with the sample event and Playwright (pip install playwright):

    python tools/check_drawings.py http://localhost:8080
"""

import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1]
S = "sample-hack-2026"
JS = """() => [...document.querySelectorAll('[data-reveal]')].map(e => {
  const r = e.getBoundingClientRect();
  return {cls: e.getAttribute('class') || e.tagName, seen: e.classList.contains('seen'), shown: r.width > 0 && r.height > 0};
})"""
PAGES = [
    (None, f"/events/{S}/"),
    (None, f"/events/{S}/results/"),
    ("organizer@example.org", f"/events/{S}/organize/results/"),
]
bad = 0
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h in ((1920, 1080), (1440, 900), (390, 844)):
        for who, path in PAGES:
            pg = b.new_context(viewport={"width": w, "height": h}).new_page()
            if who:
                pg.goto(BASE + "/accounts/login/")
                pg.fill("#id_username", who)
                pg.fill("#id_password", "plumbline")
                pg.click("main form button[type=submit]")
                pg.wait_for_load_state()
            pg.goto(BASE + path)
            pg.wait_for_load_state("networkidle")
            total = pg.evaluate("document.documentElement.scrollHeight")
            y = 0
            while y < total:
                pg.mouse.wheel(0, 400)
                y += 400
                pg.wait_for_timeout(50)
            pg.wait_for_timeout(1100)
            rows = pg.evaluate(JS)
            for r in rows:
                state = "ok" if (r["seen"] or not r["shown"]) else "NEVER ARRIVED"
                bad += state != "ok"
                print(w, path, r["cls"], "shown" if r["shown"] else "hidden at this width", state)
            if not rows:
                print(w, path, "no drawings")
    b.close()
print("problems:", bad)
sys.exit(1 if bad else 0)
