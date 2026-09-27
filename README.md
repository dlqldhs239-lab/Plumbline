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
dashboard; cross-judge normalization with a documented method and a
generated proof; CSV export at every stage; an append-only audit log an
organizer reads in the browser.

**T3 Public.** Community voting with three access modes (open link,
organizer-issued email ballot links, signed-in users), optional quadratic
voting, comments on gallery projects with organizer moderation, results hidden
from everyone but organizers until published, randomised ballot order that is
stable per voter, rate limits shared across workers, duplicate-ballot flagging
by address, and voidable ballots that are never deleted.

**Partial T4.** Every UI action is also an API call with a published OpenAPI
schema and bearer tokens. Data leaves as CSV (projects, assignments, scores,
results, calibration, votes, audit) and, for a full migration, `pg_dump`.

## Tier claim

`.dogfood.toml` claims **T1 and T2**, which is exactly what `run.py` verifies.
T3 is implemented in full but the checker has no T3 probes, so it is not
listed there; claiming a tier the receipt cannot show felt like the wrong
side of "claim your tiers honestly". To verify T3 yourself:

- run `python manage.py test tests.test_community` (14 tests: access modes,
  quadratic budget, random-but-stable order, hidden tallies, flagging, rate
  limits, voiding, comments and moderation), or
- as the organizer, open *Settings*, set *Community voting* to *Anyone with
  the link* and a voting window, then open `/events/sample-hack-2026/ballot/`
  in a private window and the *Voting* tab in the console.

## What it does not do yet

- Webhooks, certificates, signed judge participation records and an
  embeddable gallery widget (the rest of T4).
- Bulk import beyond the fixture format; export is CSV and `pg_dump`.
- File uploads: thumbnails and gallery images are URLs.
- Outbound email. Judges and ballot links are created by the organizer and
  distributed with whatever mail tool they already use.
- Pairwise (Gavel-style) judging mode.
- A multi-track judge can filter their queue by track, but there is no
  per-track leaderboard on the results page yet.

## Running it for real

Copy `.env.example` to `.env` and set:

| variable | default | notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev value | **change it** |
| `PLUMBLINE_SEED` | `1` | set `0` so the fixtures are not loaded |
| `PLUMBLINE_SEED_SECRET` | dev value | only matters when seeding |
| `ALLOWED_HOSTS` | `*` | your hostname |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8080,…` | your origin, with scheme |
| `DJANGO_DEBUG` | `0` | leave it |
| `PLUMBLINE_SITE_NAME` | `Plumbline` | shown in the header |
| `PLUMBLINE_ANON_WRITE_RATE` | `20` | anonymous writes per minute per address |
| `GUNICORN_WORKERS` | `2` | processes |
| `GUNICORN_THREADS` | `8` | threads per process; browsers hold idle connections, so keep this above 4 |

Put a TLS-terminating proxy in front of port 8080 and forward
`X-Forwarded-For` so rate limits and the audit log see real addresses.
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
python manage.py test tests            # 49 tests, ~3 minutes
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
