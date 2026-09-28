"""Every screen, as a person with a keyboard, a screen reader or a phone meets it.

Two checks on a running portal that has the two sample events:

1. axe (WCAG 2 A and AA, and its best practices) on every screen, signed out,
   as an organizer and as a judge.
2. At 390 px wide: no page scrolls sideways, no table runs off the screen
   without a box to scroll it in, and no word in a table is broken in the
   middle.

Needs Playwright and axe (pip install playwright axe-playwright-python):

    python tools/check_screens.py http://localhost:8080
"""

import sys

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
PASSWORD = "plumbline"
S, H = "sample-hack-2026", "open-house"
CONSOLE = ("", "projects/", "judges/", "rubric/", "assignments/", "results/", "records/", "voting/",
           "integrations/", "audit/", "settings/", "prizes/", "questions/", "import/")  # fmt: skip
PUBLIC = ["/", f"/events/{S}/", f"/events/{S}/gallery/", f"/events/{S}/results/", f"/events/{H}/",
          f"/events/{H}/gallery/", f"/events/{H}/ballot/", "/accounts/login/", "/accounts/signup/", "/verify/",
          "/design/", "/nothing-here/", f"/events/{S}/embed/gallery/", f"/events/{S}/embed/results/"]  # fmt: skip
ORGANIZER = ["/dashboard/", "/accounts/tokens/", "/events/new/"] + [
    f"/events/{e}/organize/{p}" for e in (H, S) for p in CONSOLE
]
JUDGE = ["/judge/", f"/judge/{H}/", f"/judge/{S}/"]
# (page to look on, link to follow): screens whose address holds a number that differs between installations
FOLLOW = {None: (f"/events/{H}/gallery/", "a.project"), "judge": (f"/judge/{H}/", "a[href*='/review/']")}

PHONE = r"""() => {
  const out = [];
  document.querySelectorAll('table').forEach((t, n) => {
    if (!t.offsetParent) return;
    let scroller = null;
    for (let e = t.parentElement; e && e !== document.body; e = e.parentElement) {
      const o = getComputedStyle(e).overflowX;
      if (o === 'auto' || o === 'scroll') { scroller = e; break; }
    }
    const past = Math.round(t.getBoundingClientRect().right - innerWidth);
    if (!scroller && past > 1) out.push('table ' + n + ' runs ' + past + 'px past the screen and nothing scrolls it');
    const walk = document.createTreeWalker(t, NodeFilter.SHOW_TEXT);
    for (let node = walk.nextNode(); node; node = walk.nextNode()) {
      const el = node.parentElement;
      if (!el || !el.offsetWidth || el.closest('code, select, textarea')) continue;
      const word = /[^\s]{3,}/g;
      let m;
      while ((m = word.exec(node.data))) {
        const r = document.createRange();
        r.setStart(node, m.index); r.setEnd(node, m.index + m[0].length);
        const lines = new Set([...r.getClientRects()].filter(q => q.width > 0).map(q => Math.round(q.top)));
        if (lines.size > 1) out.push('table ' + n + ': "' + m[0].slice(0, 40) + '" is broken over ' + lines.size + ' lines');
      }
    }
  });
  const over = document.documentElement.scrollWidth - innerWidth;
  if (over > 0) out.push('the page scrolls sideways by ' + over + 'px');
  return [...new Set(out)];
}"""

axe = Axe()
bad = 0
screens = 0


def visit(browser, width, who, paths, follow, look):
    global bad, screens
    page = browser.new_context(viewport={"width": width, "height": 900}, reduced_motion="reduce").new_page()
    if who:
        page.goto(BASE + "/accounts/login/")
        page.fill("#id_username", who)
        page.fill("#id_password", PASSWORD)
        page.click("main form button[type=submit]")
        page.wait_for_load_state()
    paths = list(paths)
    if follow:
        page.goto(BASE + follow[0])
        link = page.query_selector(follow[1])
        if link:
            paths.append(link.get_attribute("href"))
    for path in paths:
        page.goto(BASE + path)
        page.wait_for_load_state("networkidle")
        screens += 1
        for line in look(page):
            bad += 1
            print(f"FAIL {width:>4} {path} | {line}")


def with_axe(page):
    # A drawing not yet scrolled to is clipped to nothing; it is judged as it stands once shown.
    page.add_style_tag(content="html.js [data-reveal]{clip-path:none!important;opacity:1!important}")
    found = axe.run(
        page, options={"runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21aa", "best-practice"]}}
    )
    for v in found.response["violations"]:
        for node in v["nodes"]:
            checks = node.get("any") or node.get("all") or node.get("none") or [{}]
            yield f"{v['id']} ({v['impact']}) {node['target'][0]} | {checks[0].get('message', '')[:140]}"


def on_a_phone(page):
    return page.evaluate(PHONE)


with sync_playwright() as p:
    b = p.chromium.launch()
    for width, look in ((1440, with_axe), (390, on_a_phone)):
        visit(b, width, None, PUBLIC, FOLLOW[None], look)
        visit(b, width, "organizer@example.org", ORGANIZER, None, look)
        visit(b, width, "wei.lindqvist@example.org", JUDGE, FOLLOW["judge"], look)
    b.close()
print(f"{screens} screens looked at, {bad} problem{'s' if bad != 1 else ''}")
sys.exit(1 if bad else 0)
