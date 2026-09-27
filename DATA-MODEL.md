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
  │                         │                 │ ──< PairwiseComparison >── User (judge)
  │                         │                 │ ──< Record >── User (recipient)
  │                         │                 │ ──< Vote >── Voter >── Event
  │                         │                 └──< Comment >── User
  │                         └──< JudgeCalibration, AuditLog, Voter, Webhook ──< WebhookDelivery
  └──< ApiToken, SignInLink
```

## Tables

### accounts

| table | columns | notes |
|---|---|---|
| `ApiToken` | user, label, key_hash (unique), prefix, created_at, last_used_at, revoked_at | only the SHA-256 of the token is stored |
| `SignInLink` | user, key_hash (unique), created_by, created_at, expires_at, used_at | a one-time link with which an invited person sets a password. Only the hash is stored; a new link for the same person ends the earlier one |

### events

| table | columns | notes |
|---|---|---|
| `Event` | slug (unique), name, tagline, description, external_id, submissions_open_at, submissions_close_at, judging_open_at, judging_close_at, results_published_at, voting_access, voting_open_at, voting_close_at, voting_credits, comments_enabled, reviews_per_project, is_listed, theme_ground, theme_ink, theme_accent, theme_signal, created_by | every open/closed decision is a method on this row (`submissions_open()`, `judging_open()`, `voting_open()`); nothing else keeps a copy of the dates |
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
| `Rubric` | event (1:1), name, scale_min, scale_max, instructions, pairwise, jury_k | `jury_k` empty follows the event's reviews per project; 0 switches the jury-size adjustment off |
| `Criterion` | rubric, key (slug), name, description, weight (decimal), order | unique (rubric, key). Criteria are replaceable until the first score exists |
| `JudgeAssignment` | event, judge (user), project, batch, status ∈ {pending, in_progress, submitted}, comment, assigned_at, submitted_at | unique (judge, project). **Scores hang off this row**, so "a judge's scores" is always "scores of the judge's assignments" — there is no query that lists scores without going through the judge |
| `Score` | assignment, criterion, value | unique (assignment, criterion) |
| `PairwiseComparison` | event, judge, left, right, preferred?, created_at | unique (judge, left, right), and `left < right` by a check constraint, so a pair has one row per judge whichever way round it was shown. `preferred` empty means the judge could not say |
| `ProjectResult` | event, project (1:1), review_count, raw_mean, normalized_mean, adjusted_mean, rank_raw, rank_normalized, rank, criterion_means (JSON), jury_k, pairwise_score, pairwise_rank, pairwise_n, pairwise_wins, community_score, method, computed_at | a snapshot written by *Recompute*; not a source of truth. `rank` is the one that stands; the other two ranks say what each earlier step would have given. `jury_k` is the constant the row was computed with |
| `JudgeCalibration` | event, judge, review_count, mean, stdev, shrink_weight, shrunk_mean, shrunk_stdev, flat | per-judge statistics from the last recompute, drawn on the organizer's results page |

### community

| table | columns | notes |
|---|---|---|
| `Voter` | event, kind ∈ {open, email, auth}, key (unique), user?, email, ballot_token, ip_hash, ua_hash, created_at, last_seen_at, voided_at, void_reason, flags (JSON list) | one ballot-holder. IP and user-agent are stored only as keyed SHA-256 hashes, enough to detect duplicates, not enough to identify a person. Unique (event, user) and (event, email) where present |
| `Vote` | event, voter, project, weight, created_at, updated_at | unique (voter, project). `weight` is 0/1 in plain mode; with quadratic voting the cost is weight² |
| `Comment` | project, author, body, created_at, hidden_at, hidden_by | moderation hides, never deletes |

### records

| table | columns | notes |
|---|---|---|
| `Record` | serial (unique), kind ∈ {placement, participation, judge}, event, recipient, project?, payload (JSON), signature, issued_by, issued_at, revoked_at, revoke_reason | the payload is what is signed. Never deleted: withdrawn, with a reason. A record of a place stands only while the results are published and the project is in them |

### integrations

| table | columns | notes |
|---|---|---|
| `Webhook` | event, url, description, secret, actions (JSON list of prefixes), active, created_by, created_at | the secret is shown in full once |
| `WebhookDelivery` | webhook, entry?, action, payload (JSON), status ∈ {pending, ok, failed}, status_code, attempts, error, response_excerpt, created_at, last_attempt_at | a row per attempt target, so an organizer can see what was sent and retry |

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
- A submitted review is closed, for everyone.
- A judge compares only projects assigned to them, and answers each pair once.
- A project cannot be submitted while a required question of the organizer's
  is unanswered.
- Addresses that are printed as links are http or https.
- Records are issued only for published results, and there is never more
  than one standing record for one person and one project.

## Getting data in

- **Fixture import** — `python manage.py seed_fixtures path/to/fixtures.json`
  loads the DOGFOOD fixture format (event, tracks, judges, teams, projects,
  scores). Keyed on the fixture ids, so it can be re-run. This is also how the
  Docker image seeds itself at boot.
- **CSV import**: console, *Settings*, *Import*, or
  `POST /api/events/<slug>/import`. Projects with their teams and members,
  or judges with their tracks. The file is checked first and the check
  writes nothing; the import is all of the file or none of it. Comma,
  semicolon or tab; a byte order mark is ignored; the template can be
  downloaded from the page.
- **Judges**: one at a time by email in the console or `POST
  /api/events/<slug>/judges`, or in bulk with the import.
- **Ballot voters**: paste a list of emails in *Voting*; one link each.
- **Anything else**: the Django admin at `/admin/` exposes every table.

## Getting data out

Every stage has a CSV, from the organizer console or the API
(`GET /api/events/<slug>/export/<kind>.csv`):

| kind | rows |
|---|---|
| `projects` | one per project, with team, track, links, tags, status, member emails |
| `assignments` | one per judge×project, with batch and status |
| `scores` | one per assignment with a column per criterion and the comment |
| `results` | standings: three ranks, three scores, review count, method |
| `calibration` | per-judge mean, spread, shrink weight, flat flag |
| `audit` | the complete audit log for the event |
| votes, ballot links | from the *Voting* tab |

A cell that a spreadsheet would run as a formula (a team that calls itself
`=HYPERLINK(...)`) is written with a leading apostrophe, which spreadsheets
read as "this is text".

Signed records leave as JSON, one per record, from the certificate page or
`GET /api/records/<number>`.

For a complete migration, `pg_dump` the `plumbline` database; every table
above is plain relational data with no application-specific encoding beyond
two JSON list columns (`image_urls`, `tech_tags`) and the audit `detail`
blob.
