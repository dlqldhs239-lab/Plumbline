# Architecture

## In one paragraph

Plumbline is a single Django application in front of PostgreSQL, served by
gunicorn, shipped as one container plus the database. Pages are rendered on
the server; the same operations are exposed as a REST API through
django-ninja. All writes go through a service layer that enforces the rules
and appends to an audit log, so the UI, the API, the admin and the seed
command cannot disagree about what is allowed. There is no queue, no cache
server, no mail server and no JavaScript build: the goal was a portal an
organizer can run on Monday and hand to someone else without a call.

The reasons behind each choice, and what was turned down, are in
[DECISIONS.md](DECISIONS.md).

```
browser / curl / run.py
        │
        ▼
 gunicorn (2 workers x 8 threads)  ── whitenoise serves /static
        │
        ▼
 Django ─┬─ templates (server-rendered pages, SVG charts, three small scripts)
         ├─ api/ (django-ninja, OpenAPI at /api/docs)
         ├─ events/services.py      events, teams, projects, deadlines, judges
         ├─ events/extras.py        prizes, the organizer's form questions
         ├─ events/importer.py      CSV import: check first, then all or nothing
         ├─ judging/services.py     rubric, assignment, scoring, results
         ├─ judging/normalization.py  judge z-scores, shrinkage, jury size
         ├─ judging/pairwise.py     Bradley-Terry estimator
         ├─ community/services.py   voting, comments, rate limits
         ├─ records/services.py     signed certificates and judge records
         ├─ integrations/services.py  webhooks on the audit stream
         └─ audit/services.py       append-only log, called by every service
        │
        ▼
 PostgreSQL (data + cache table for rate limits)
```

## Why these choices

**Django, server-rendered.** The brief scores correctness and operability
above novelty, and rewards a judge console that makes thirty reviews bearable.
Django's authentication, sessions, permissions, forms, migrations and admin
are twenty years old and boring, which is what you want under a judging
system. Server rendering means the role checks that decide what a page shows
are the same Python functions that decide what the API returns; there is no
second copy of the rules in a frontend.

**One service layer.** `events.services`, `judging.services` and
`community.services` are the only code that mutates state. A view collects
input and renders; an API endpoint parses JSON and serialises; both call the
same function, which checks the deadline or the role, writes, and records an
audit entry. This is how "role isolation in the backend" is kept true rather
than asserted: there is no path to a score that bypasses
`assignments_for_judge()`.

**PostgreSQL, with SQLite for local hacking.** `DATABASE_URL` selects the
database. Docker always uses PostgreSQL; developers can run without it.
Nothing in the schema is database-specific.

**django-ninja for the API.** It gives typed request and response schemas and
an OpenAPI document with almost no ceremony, and it sits inside the same
process, so the API reuses the session and the service layer. Swagger UI is
served from the package's own static files; the docs page works with the
network off.

**Two ways to authenticate, one rule for CSRF.** Bearer tokens (SHA-256
hashed at rest, shown once) for the checker and integrations; the browser
session for people. A session-authenticated *unsafe* API call must carry a
CSRF token; a bearer call never needs one because no cross-site form can add
an `Authorization` header. This is implemented in `api/auth.py`, in one place.

**Database cache for rate limits.** gunicorn runs several workers; an
in-process counter would let a client exceed the limit by spreading requests
across workers. Django's database cache is shared, needs no extra service and
is created by the entrypoint. It is the only cache in the system and it holds
nothing but counters.

**No outbound email.** An organizer already has a mail tool; the portal would
need SMTP credentials, a hosted service, or a container that is one more
thing to run. Instead, judges are added by email address and the organizer
passes on a one-time sign-in link; email ballot links are generated and
exported as CSV for the organizer to send.

**Threaded workers.** The portal is reached by browsers directly. A browser
opens spare connections and leaves them idle, and with gunicorn's default
worker each idle connection holds a whole worker until it times out. Two of
them and the site stops answering. `gthread` workers keep idle connections
in threads.

**Seeding once.** `seed_fixtures` loads the organizer's fixture file the
first time and never writes again: a restart must not put back a score
someone edited or an account someone removed. In Docker the first load also
computes and publishes the results, so the sample is a finished event. The
event's close date comes from the file, so the closed event check passes
without touching a clock. The four checker tokens are derived from
`PLUMBLINE_SEED_SECRET`, so a committed `.dogfood.toml` still matches after
`docker compose down -v`.

**Charts drawn on the server.** The results slopegraph, the judge elevation
and the date rule are SVG and HTML built in Python and rendered in the
template. They are there with scripts switched off, they print, and what
they show is tested like any other function. The one canvas, on the landing
page, draws the same judges as plumb lines and stops when it has settled.

## Apps

| app | owns |
|---|---|
| `accounts` | API tokens, one-time sign-in links, sign-up, email-or-username login |
| `events` | Event, Track, Prize, EventRole, Team, TeamMembership, TeamInvite, Project, CustomQuestion/Answer; role checks (`permissions.py`); write rules (`services.py`); public pages, participant pages, organizer console |
| `judging` | Rubric, Criterion, JudgeAssignment, Score, PairwiseComparison, ProjectResult, JudgeCalibration; assignment, scoring, normalization (`normalization.py`), pairwise ranking (`pairwise.py`), what the public pages show (`showcase.py`), CSV export; judge console |
| `community` | Voter, Vote, Comment; access modes, quadratic budget, ballot order, tallies, integrity report, moderation |
| `records` | Record; issuing, withdrawing and checking signed certificates and judge participation records |
| `integrations` | Webhook, WebhookDelivery; signing, delivery, retry |
| `audit` | AuditLog; `record()` helper; middleware that makes the current actor and IP available to the service layer |
| `api` | the ninja router, schemas and auth classes |

## Request lifecycle for the two things that matter most

**A judge scores a project.** `POST /judge/<event>/review/<id>/` or
`POST /api/judges/me/assignments/<id>/scores` → `get_own_assignment(user, id)`
loads the assignment *filtered by judge and by the judge's allowed tracks*
(anything else is a 403 before a score is read) → `save_scores()` checks the
judging window, the scale, and completeness on submit → writes `Score` rows
→ `audit.record("score.submit")`.

**Anyone asks for a judge's scores.** `GET /api/judges/<ref>/scores` resolves
the judge, then: the judge themself → their own view; an organizer with
`?event=` → that event only; an admin → everything; anyone else → 403. The
UI has no page for another judge's scores at all; organizers see aggregated
progress and export scores as CSV.

## Deployment shape

`compose.yaml` runs `db` (postgres:17-alpine, health-checked, named volume)
and `web` (this image). The entrypoint migrates, creates the cache table,
seeds unless `PLUMBLINE_SEED=0`, prints the checker headers and starts
gunicorn. Static files are collected at build time and served by whitenoise,
so no separate web server is required; put a TLS proxy in front for the
public internet.

## How it was checked

- 382 tests, run on every change. Among them a second implementation of the
  normalization and one of the Bradley-Terry estimator, each written from the
  document and not from the module.
- The official checker (T1, T2) and `tools/verify_tiers.py` (T3, T4), against
  the Docker stack, with both reports committed.
- A browser driven through every flow by script after each stage, and
  screenshots of every page at 390, 1440 and 1920 pixels checked for overflow.
- Four independent reviews by a reviewer that had not written the code, each
  after a stage of work. They found 27, 14, 20 and then the findings of the
  fourth; every one is fixed and has a test named for it
  (`tests/test_review_*.py`).

## What we would do next

- Judge reliability in the pairwise model, so that a judge who contradicts
  the panel counts for less.
- Confidence intervals on the adjusted score, so the results page can say
  when two projects cannot be told apart.
- Image uploads with a local volume, behind the same URL fields.
- Records signed with a public key, so that a check needs no call to the
  portal that issued them.
