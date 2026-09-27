# Plumbline

A self-hostable hackathon submission and judging portal. Built in 72 hours for
[DOGFOOD 2026](https://dogfoodhack.com/) by Hackathon Raptors.

A plumb line is the weighted string a builder hangs to check that a wall is
truly vertical. This portal is built around the same idea: judging that is
straight because the backend holds the line, not because a button is hidden.

## Run it

```sh
docker compose up
```

That is the whole procedure. It builds the image, starts PostgreSQL, applies
migrations, loads the organizer's `fixtures.json` (idempotently), prints the
four auth headers used by `.dogfood.toml`, and serves on
<http://localhost:8080>. No network is needed after the image is built.

Then check it:

```sh
python3 checker/run.py .dogfood.toml --fixtures fixtures.json > acceptance-report.txt
```

The committed [`acceptance-report.txt`](acceptance-report.txt) is that command's
output against this repository.

### Seeded logins

All seeded accounts use the password `plumbline`. Sign in with email or username.

| role | email | what you can do |
|---|---|---|
| admin | `admin@example.org` | everything, plus `/admin/` |
| organizer | `organizer@example.org` | the organizer console for *Sample Hack 2026* |
| judge | `tomas.varga@example.org` (jdg_01) | the judge console; one assignment |
| judge | `wei.lindqvist@example.org` (jdg_02) | the judge console; six assignments |
| participant | `priya1@example.org` | team *NorthKiln*, project *Glass Signal* |

Every fixture judge and team member exists as a user with the same password.

### Where things are

| | |
|---|---|
| Gallery | `/events/sample-hack-2026/gallery/` |
| Organizer console | `/events/sample-hack-2026/organize/` |
| Judge console | `/judge/` |
| Community ballot | `/events/sample-hack-2026/ballot/` (the sample event has voting switched off; turn it on in Settings) |
| API docs (OpenAPI) | `/api/docs` — served from the image, no CDN |
| Django admin | `/admin/` |

## What it does

**T1 Core.** Accounts and sessions; five roles (visitor, participant, judge,
organizer, admin); events with UTC dates, tracks and prizes; teams formed by
invite link; project submission with the full standard field set plus
organizer-defined custom questions; draft-and-edit until the deadline, which
the server enforces the moment it passes; a public gallery with search and
track filter.

**T2 Judging.** Judge invitation by email with optional track restriction;
balanced automatic assignment (k reviews per project, even load, track-aware,
never your own team, reproducible with a seed) plus manual assignment; a
weighted, organizer-configurable rubric; a judge console designed for scoring
thirty projects in a sitting (keyboard scoring, submit-and-next); role
isolation enforced in the backend (a judge can only ever load their own
assignments, through the UI, the API and the exports); a live progress
dashboard; cross-judge normalization and a jury-size adjustment, with a
documented method, a generated proof and a second implementation that
checks the first; results published as adjusted score, scale and number of
judges, with the mean of every criterion and a per-track view; CSV export at every stage; an append-only audit log an
organizer reads in the browser.

**T3 Public.** Community voting with three access modes (open link,
organizer-issued email ballot links, signed-in users), optional quadratic
voting, comments on gallery projects with organizer moderation, results hidden
from everyone but organizers until published, randomised ballot order that is
stable per voter, rate limits shared across workers, duplicate-ballot flagging
by address, and voidable ballots that are never deleted.

**T4 Stretch.** Every UI action is also an API call with a published OpenAPI
schema and bearer tokens. Data leaves as CSV (projects, assignments, scores,
results, calibration, votes, audit) and, for a full migration, `pg_dump`.
Webhooks subscribe to an event's audit stream: signed with HMAC-SHA256, sent
after the change is committed, recorded per delivery and retryable. Invited
judges get in through a one-time sign-in link the organizer passes on.

**Signed records (T4).** Once results are published the organizer issues a
record to every member of every ranked team and to every judge who submitted
a review. Each opens as a certificate that prints on one page, carries a
number and an HMAC-SHA256 signature, and can be checked by anyone at
`/verify/` or `POST /api/records/check`: genuine, withdrawn, altered or
unknown. A judge's record says that they judged and how much, never what
they scored. A record whose place changed after a recompute is withdrawn and
replaced, so there is never more than one to believe.

**Pairwise mode (bonus).** Beside the rubric, judges can be asked which of
two of their projects is the better one. The comparisons are ranked with a
Bradley-Terry model (MM algorithm, a weak prior so that unbeaten projects
and disconnected groups stay finite) and shown next to the rubric ranking
with their agreement. Method and tests in [JUDGING.md](JUDGING.md), section
5; the model on the fixture data in
[docs/pairwise-proof.md](docs/pairwise-proof.md).

## Tier claim

`.dogfood.toml` claims **T1, T2, T3 and T4**.

| tier | verified by | report |
|---|---|---|
| T1, T2 | the official `checker/run.py`, 7 probes | `acceptance-report.txt` |
| T3, T4 | `tools/verify_tiers.py`, 51 probes in the same manner | `acceptance-report-t3-t4.txt` |

The official checker has probes for T1 and T2 only, so it prints T3 and T4
as *claimed but not verified*. That line is true of the checker, not of the
portal. Rather than ask for the two upper tiers to be taken on trust, the
repository carries a second program that does for them what `run.py` does
for the lower two: it reads the same `.dogfood.toml`, uses the same four
headers, makes requests to the running portal, and prints PASS or FAIL.
It needs only the standard library.

```sh
python checker/run.py .dogfood.toml --fixtures fixtures.json
python tools/verify_tiers.py .dogfood.toml
```

What each upper tier asks for, and where it is:

| asked for | where |
|---|---|
| T3 community voting: email gated, link based or authenticated | Settings, *Who may vote*; `/events/<slug>/ballot/` |
| T3 project comments | project page; `POST /api/events/<slug>/projects/<id>/comments` |
| T3 results hidden during the voting window | 403 on the page and the API until published |
| T3 randomized project ordering on ballots | per voter, stable across reloads |
| T3 anti abuse: rate limits, duplicate detection, audit trail | [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md); console, *Voting* and *Audit log* |
| T4 REST API and webhooks | `/api/docs`; console, *Integrations* |
| T4 certificate and record generation | console, *Records*; `/records/<number>/` |
| T4 signed, publicly verifiable judge participation records | `/verify/`; `POST /api/records/check` |
| T4 embeddable gallery widget | `/events/<slug>/embed/gallery/`; snippet in *Integrations* |
| T4 bulk import and export | console, *Settings*, *Import*; `POST /api/events/<slug>/import`; CSV exports |

## What it does not do yet

- File uploads: thumbnails and gallery images are URLs.
- Outbound email. Judges and ballot links are created by the organizer and
  distributed with whatever mail tool they already use.

## Before a real event

`docker compose up` with no settings is a **sample installation**: the
secret key, the seeded accounts (password `plumbline`, one of them an
administrator) and the four API tokens are the ones printed in this README
and in `.dogfood.toml`. That is what makes the checker work on a fresh
clone, and it is fine on your own machine. The container says so at start,
and staff see a notice on every page until it is no longer true.

For a real event set `DJANGO_SECRET_KEY`, `PLUMBLINE_SEED=0` and
`ALLOWED_HOSTS`, create your own administrator, and remove the seeded
accounts if the sample was ever loaded.

## Running it for real

Copy `.env.example` to `.env` and set:

| variable | default | notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev value | **change it** |
| `PLUMBLINE_SEED` | `1` | set `0` so the fixtures are not loaded |
| `PLUMBLINE_SEED_PUBLISH` | `1` | the sample event is loaded finished, with results published; `0` leaves that to the organizer |
| `PLUMBLINE_SEED_SECRET` | dev value | only matters when seeding |
| `ALLOWED_HOSTS` | `*` | your hostname |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8080,…` | your origin, with scheme |
| `DJANGO_DEBUG` | `0` | leave it |
| `PLUMBLINE_SITE_NAME` | `Plumbline` | shown in the header |
| `PLUMBLINE_ANON_WRITE_RATE` | `20` | anonymous writes per minute per address |
| `PLUMBLINE_TRUST_PROXY` | `0` | set `1` only behind a proxy you run; see below |
| `PLUMBLINE_WEBHOOK_ALLOW_PRIVATE` | `0` | set `1` if webhook receivers live on your own network |
| `PLUMBLINE_PORT` | `8080` | the port on the host |
| `GUNICORN_WORKERS` | `2` | processes |
| `GUNICORN_THREADS` | `8` | threads per process; browsers hold idle connections, so keep this above 4 |

Put a TLS-terminating proxy in front of port 8080, have it set
`X-Forwarded-For`, and set `PLUMBLINE_TRUST_PROXY=1` so rate limits and the
audit log see real addresses. Leave it at `0` when the port is reached
directly: the header is then whatever the caller typed.

The fixtures are loaded once. Later starts read them only to print the
checker headers; they never put back a score, a judge or an account that
someone has changed or removed since. The seeded accounts share a published
password, so for a real event start with `PLUMBLINE_SEED=0`.
Create the first organizer with `docker compose exec web python manage.py createsuperuser`,
then create an event at `/events/new/`.

Backups: `docker compose exec db pg_dump -U plumbline plumbline > backup.sql`.
Leaving: the CSV exports in the organizer console cover every table an
organizer cares about; `pg_dump` covers the rest.

## Development

```sh
pip install -r requirements.txt
python manage.py migrate && python manage.py createcachetable
python manage.py seed_fixtures fixtures.json
python manage.py runserver 8080
python manage.py test tests            # 254 tests, 3 to 10 minutes
python manage.py normalization_report sample-hack-2026 > docs/normalization-proof.md
```

Without `DATABASE_URL` the development server uses SQLite; Docker uses PostgreSQL.

## Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — the shape of the system and why
- [DATA-MODEL.md](DATA-MODEL.md) — schema, and how data gets in and out
- [JUDGING.md](JUDGING.md) — assignment strategy, scoring maths, normalization, defended
- [docs/normalization-proof.md](docs/normalization-proof.md) — the method run on the fixture data
- [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md) — voting and submission abuse: what is stopped, what is not

## License

MIT. `fixtures.json` and `checker/run.py` are the organizer's files, included
unchanged so the checker runs from a clean clone.
