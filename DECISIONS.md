# Decisions

What was chosen, what was turned down, and why. Each entry is short enough
to disagree with. Where a decision has a cost we still carry, the entry says
so.

## 1. One process, one database, server-rendered pages

**Chosen.** Django with PostgreSQL, templates rendered on the server, one
stylesheet, three small scripts, no build step.

**Turned down.** A JavaScript front end talking to the API; a queue and a
worker for background work.

**Why.** An organizer who adopts this has to keep it running for years with
whoever is around. `docker compose up` starts two containers and that is the
whole system. Every page works with scripts switched off, because the charts
are SVG drawn on the server. The API is complete, so a front end can be
built on it by someone who wants one.

**Cost.** Webhook deliveries run in a thread of the web process. A restart
during a delivery leaves it pending; `manage.py deliver_webhooks` picks it up.

## 2. The portal sends no email

**Chosen.** No outbound mail at all. Invited judges get a one-time sign-in
link that the organizer passes on; email ballots are links in a CSV.

**Turned down.** SMTP settings and a mail queue.

**Why.** Mail is where self-hosted tools go to die: deliverability, SPF,
bounces, a provider account. Organizers already have a way to reach their
judges. The rule was also "runs with no network", and mail cannot.

**Cost.** Email ownership is never verified. Whoever invites an address
first decides who receives its link. The threat model says so.

## 3. Rules live in one layer

**Chosen.** Every write goes through a function in `*/services.py`, which
checks the role, checks the rule it is named after, and writes the audit
entry. Pages and API endpoints call those functions and nothing else.

**Why.** "The UI hides the button" is not isolation. With one layer, a rule
holds the same way from the page, the API, a script and the admin, and a
reviewer has one place to read.

**Evidence.** The settings page used to save through the form directly and
logged only one field. An independent review found it; it now goes through
the same function as the API.

## 4. We claim all four tiers, and wrote the probes the checker lacks

**Chosen.** `.dogfood.toml` claims T1 to T4. The official checker verifies
T1 and T2. `tools/verify_tiers.py` verifies T3 and T4 with 55 probes written
in the checker's manner, and its output is committed beside the official
report.

**Turned down.** Claiming only T1 and T2, which is what we did for the first
eighteen hours. The reasoning then: the acceptance report is the claim, and
a tier the report cannot show is a tier we are asking to be believed about.

**Why we changed.** The rules say teams are judged on the tiers they claim.
Leaving out two tiers that are built, tested and documented would understate
the work as surely as claiming unbuilt ones would overstate it. The honest
answer to "the checker cannot see it" was to make it visible to a program,
not to stay silent about it.

**Cost.** The official report now ends with "claimed but not verified: T3
T4". The README says why in its first screen.

## 5. Scores are corrected in two steps, and both can be read

**Chosen.** Per-judge standard scores with shrinkage toward the panel, then
an adjustment for how many reviews a project received. Raw mean, normalized
score and adjusted score are all stored, each with the rank it would give.

**Turned down.** Raw averages. Dropping the highest and lowest mark (with
two to five reviews there is nothing left). Pairwise comparison (a different
judging flow; listed as future work). A model fitted by iteration (hard to
explain to a team that lost a place).

**Why.** The method has to be explainable to the team it moved. Every step
is a formula with one constant, the page shows a project going through all
three, and `JUDGING.md` has a case small enough to check with a pencil.

**Evidence.** A second implementation, written from the document with the
standard library's statistics functions, agrees with the module to nine
decimal places on the fixture set for five settings of the constants.

## 6. A judge who gives everyone the same mark counts as no opinion

**Chosen.** Their reviews get a standard score of zero and the judge is
flagged to the organizer.

**Turned down.** Dividing by a spread borrowed from the panel, which would
manufacture a preference the judge never expressed. Discarding their
reviews, which would change review counts silently.

**Why.** A four given to everything says nothing about which project is
better. It is information about the judge, and it is shown as that.

## 7. The jury-size constant follows the event

**Chosen.** By default the panel mean counts for as many reviews as the
event asks for per project. The organizer can set it, or set it to zero.

**Why.** The host publishes its own results with this adjustment at ten,
for panels of about nine judges a project. The same ratio with three reviews
a project is three. A fixed ten would flatten an event with small panels
until the order depended mostly on review counts.

## 8. The community vote is never mixed into the judged score

**Chosen.** Two columns. Organizers who want a blend have both in the CSV.

**Why.** Blending lets the least controlled signal move the most controlled
one. An audience prize is a different prize.

## 9. Flag, do not block

**Chosen.** Several ballots from one address are flagged for the organizer.
Two submissions sharing a repository are shown to the organizer. Nothing is
rejected automatically except a second submission from the same team.

**Why.** Shared offices, university networks and teams forking the same
starter are all real. A false positive that silently removes a real vote or
a real project is worse than a decision an organizer has to make, and the
decision is logged.

## 10. Feedback reaches the team, without the judge's name

**Chosen.** After publication a team reads every judge's marks and comment
for its own project, labelled Judge 1, Judge 2, in an order that is neither
assignment order nor submission order. Everyone else sees criterion means.

**Turned down.** Publishing judge comments on the results page, as the host
does for winners.

**Why.** A judge writing to a team writes differently from a judge writing
for the public. Organizers can quote from the CSV with the judge's consent.

## 11. The fixtures are loaded once

**Chosen.** The loader writes only while the fixture event does not exist.
On the first load in Docker it also computes and publishes the results, so
the sample is a finished event.

**Why.** It used to write on every start. A restart would undo edited
scores, bring back a deleted administrator with a published password, and
promote whoever had since registered that address. A review found it.

## 12. Each event has its own colours, and unreadable ones are refused

**Chosen.** Four colours per event; everything else is derived. A theme is
refused at save time, in the form and in the API, if text, secondary text,
accent, signal or button text falls under WCAG AA on that ground.

**Why.** The host runs a dozen events a year and each has its own identity.
A platform they adopt has to wear them. Letting an organizer pick colours
without checking is how a results page ends up grey on grey.

## 13. One signature per screen

**Chosen.** The landing page has the plumb lines; the results page the
slopegraph; the organizer's results page the elevation; the review form the
graduated scale; the event page the date rule. Nothing else on those screens
moves or decorates.

**Why.** The name is the idea: a plumb line shows how far something is out
of true, and that is what the normalization does. Used once per screen it
reads; used everywhere it would be wallpaper.

## 14. What we would do differently

- **Review before building on top.** The first independent review came
  after three tiers were written and found twenty-seven problems, three of
  them serious. Reviews now follow each stage.
- **Line endings.** Patch scripts on Windows turned the container's
  entrypoint into CRLF and the image stopped starting. `.gitattributes`
  and a guard in the Dockerfile came after the fact.
- **Prizes and organizer questions** are edited in the Django admin. They
  deserve pages of their own in the console.
