# Data model

All ids are integers; every fixture object keeps its original id in an
`external_id` column so imports are idempotent and exports can be joined back
to the source. All timestamps are UTC (`USE_TZ`), stored with time zone.

```
User ──< EventRole >── Event ──< Track
  │        (role, tracks*)  │ ──< Prize
  │                         │ ──< CustomQuestion ──< CustomAnswer >── Project
  │                         │ ──< Team ──< TeamMembership >── User
  │                         │       │ ──< TeamInvite
  │                         │       └──< Project ──< JudgeAssignment >── User (judge)
  │                         │                 │           └──< Score >── Criterion >── Rubric (1:1 Event)
  │                         │                 │ ──1 ProjectResult
  │                         │                 │ ──< Vote >── Voter >── Event
  │                         │                 └──< Comment >── User
  │                         └──< JudgeCalibration, AuditLog, Voter
  └──< ApiToken
```

## Tables

### accounts

| table | columns | notes |
|---|---|---|
| `ApiToken` | user, label, key_hash (unique), prefix, created_at, last_used_at, revoked_at | only the SHA-256 of the token is stored |

### events

| table | columns | notes |
|---|---|---|
| `Event` | slug (unique), name, tagline, description, external_id, submissions_open_at, submissions_close_at, judging_open_at, judging_close_at, results_published_at, voting_access, voting_open_at, voting_close_at, voting_credits, comments_enabled, reviews_per_project, is_listed, created_by | every open/closed decision is a method on this row (`submissions_open()`, `judging_open()`, `voting_open()`); nothing else keeps a copy of the dates |
| `Track` | event, name, description, external_id, order | unique (event, name) |
| `Prize` | event, track?, name, description, amount, currency, order | |
| `EventRole` | event, user, role ∈ {participant, judge, organizer}, tracks (M2M), external_id | unique (event, user, role). Admin is `User.is_superuser`, not an EventRole. A judge with no tracks sees every track |
| `Team` | event, name, external_id, created_by | names are **not** unique: the fixture set has three teams called StillTrail, and so do real events |
| `TeamMembership` | team, user, role ∈ {owner, member}, joined_at | unique (team, user); the service layer also enforces one team per user per event |
| `TeamInvite` | team, token (unique), created_by, expires_at, max_uses, uses, revoked_at | a join link |
| `Project` | event, team, track?, external_id, title, tagline, description, thumbnail_url, image_urls (JSON list), demo_video_url, repo_url, live_url, tech_tags (JSON list), status ∈ {draft, submitted, withdrawn}, submitted_at, is_hidden, duplicate_of?, created_by | `is_public` = submitted and not hidden. `duplicate_of` is set by the seed when a team submits the same title twice; duplicates stay out of the gallery, assignment and results |
| `CustomQuestion` | event, prompt, help_text, kind ∈ {text, textarea, url, choice, checkbox}, choices (JSON), required, order | organizer-defined submission fields |
| `CustomAnswer` | project, question, value | unique (project, question) |

### judging

| table | columns | notes |
|---|---|---|
| `Rubric` | event (1:1), name, scale_min, scale_max, instructions | |
| `Criterion` | rubric, key (slug), name, description, weight (decimal), order | unique (rubric, key). Criteria are replaceable until the first score exists |
| `JudgeAssignment` | event, judge (user), project, batch, status ∈ {pending, in_progress, submitted}, comment, assigned_at, submitted_at | unique (judge, project). **Scores hang off this row**, so "a judge's scores" is always "scores of the judge's assignments" — there is no query that lists scores without going through the judge |
| `Score` | assignment, criterion, value | unique (assignment, criterion) |
| `ProjectResult` | event, project (1:1), review_count, raw_mean, normalized_mean, rank_raw, rank_normalized, community_score, method, computed_at | a snapshot written by *Recompute*; not a source of truth |
| `JudgeCalibration` | event, judge, review_count, mean, stdev, shrink_weight, flat | per-judge statistics from the last recompute, shown on the results page |

### community

| table | columns | notes |
|---|---|---|
| `Voter` | event, kind ∈ {open, email, auth}, key (unique), user?, email, ballot_token, ip_hash, ua_hash, created_at, last_seen_at, voided_at, void_reason, flags (JSON list) | one ballot-holder. IP and user-agent are stored only as keyed SHA-256 hashes, enough to detect duplicates, not enough to identify a person. Unique (event, user) and (event, email) where present |
| `Vote` | event, voter, project, weight, created_at, updated_at | unique (voter, project). `weight` is 0/1 in plain mode; with quadratic voting the cost is weight² |
| `Comment` | project, author, body, created_at, hidden_at, hidden_by | moderation hides, never deletes |

### audit

| table | columns | notes |
|---|---|---|
| `AuditLog` | created_at, actor?, actor_label, event?, action, target_type, target_id, target_label, detail (JSON), ip_address, channel ∈ {ui, api, seed, admin, system} | append-only; the admin registration refuses add/change/delete. Indexed on (event, created_at) for the organizer's log page |

## Invariants the service layer keeps

- A project can be created, edited, submitted or withdrawn only while
  `submissions_open_at ≤ now < submissions_close_at`, or by an organizer.
- A user is on at most one team per event; joining is refused after the
  deadline; invite links expire and have a use cap.
- A judge can read or write only assignments where `judge = user`, further
  filtered to their tracks. Organizers of the event and admins may read
  everything; nobody else can.
- A judge is never assigned a project from a team they belong to.
- Scores must lie within the rubric scale; a review can be submitted only
  with every criterion filled.
- Results, tallies and the community vote count are visible only to
  organizers until `results_published_at` is set.
- A voter's total quadratic cost never exceeds `voting_credits`.
- Votes and comments are never deleted by moderation; ballots are voided and
  comments hidden, with the actor recorded.

## Getting data in

- **Fixture import** — `python manage.py seed_fixtures path/to/fixtures.json`
  loads the DOGFOOD fixture format (event, tracks, judges, teams, projects,
  scores). Keyed on the fixture ids, so it can be re-run. This is also how the
  Docker image seeds itself at boot.
- **Judges** — added by email in the organizer console or created in bulk
  through `POST /api/...` (judge invitation is also available through the
  admin).
- **Ballot voters** — paste a list of emails in *Voting*; one link each.
- **Anything else** — the Django admin at `/admin/` exposes every table with
  inline editors for tracks, prizes, questions, members and criteria.

## Getting data out

Every stage has a CSV, from the organizer console or the API
(`GET /api/events/<slug>/export/<kind>.csv`):

| kind | rows |
|---|---|
| `projects` | one per project, with team, track, links, tags, status, member emails |
| `assignments` | one per judge×project, with batch and status |
| `scores` | one per assignment with a column per criterion and the comment |
| `results` | standings: both ranks, both means, review count, method |
| `calibration` | per-judge mean, spread, shrink weight, flat flag |
| `audit` | the complete audit log for the event |
| votes, ballot links | from the *Voting* tab |

For a complete migration, `pg_dump` the `plumbline` database; every table
above is plain relational data with no application-specific encoding beyond
two JSON list columns (`image_urls`, `tech_tags`) and the audit `detail`
blob.
