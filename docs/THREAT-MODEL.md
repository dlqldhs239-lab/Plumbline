# Threat model: voting and submission abuse

Written for an organizer deciding whether to trust the numbers. Each attack
says what Plumbline does about it and, where the honest answer is "not
much", says that.

## Assets

- The ranking judges produce, and the community tally.
- Judges' individual scores, which must not leak to other judges or teams.
- The submission deadline.
- Unpublished results.

## Attackers

- **A participant** who wants their project to win.
- **A judge** who wants to see or influence other judges' ballots.
- **A stranger on the internet** with the ballot link and a script.
- **A curious organizer** — not an attacker, but the audit log is designed
  so that what they did is visible to the other organizers.

## Attacks and answers

### Ballot stuffing (open voting)

*Attack:* open the ballot link in many private windows, or script it, and
vote for yourself each time.

*Answer:* every ballot is a `Voter` row keyed by a signed cookie; the client
address and user-agent are stored as keyed hashes. A second ballot from the
same address is flagged `shared_ip`, a fourth `many_ballots_same_ip`, and the
*Voting* tab lists addresses holding several ballots. Admission is rate-limited
to 10 new ballots per address per minute and votes to 20 per minute, counted
in a database-backed cache shared by all workers. Organizers void ballots;
voided votes stay in the table but leave the tally.

A signed-in caller holds one ballot per account however they arrive: the
browser and an API token of the same account share it, and a ballot opened
before signing in becomes the account's. Two requests from one ballot are
counted one after the other, so the quadratic budget holds when they arrive
together.

*Not stopped:* a botnet with many addresses, or a mobile carrier's NAT that
makes a whole city look like one address (which is why flagged ballots are
not auto-rejected). If the event matters, use **email ballot links** or
**signed-in voting** instead of the open mode; the open mode exists for
low-stakes audience-choice prizes.

### Sybil accounts (authenticated voting)

*Attack:* register fifty accounts and vote with each.

*Answer:* one ballot per account, and sign-up has the same per-address rate
limit as everything else. Quadratic voting caps the influence of each
account regardless of how many votes it spends.

*Not stopped:* patient account farming from many addresses. There is no
CAPTCHA and no email verification (the portal sends no mail). Organizers who
need real identity should issue email ballot links from their attendee list.

### Forged or shared email ballot links

*Attack:* guess another voter's link, or forward yours.

*Answer:* tokens are 24 random bytes; guessing is not practical. A link
binds the browser that opens it; a forwarded link lets the recipient vote as
the original person, which is the same trust the organizer placed in the
email address. The link CSV is generated only for organizers and every export
is logged.

### Position bias

*Attack:* not an attacker, but the same effect: whatever is listed first gets
more votes.

*Answer:* the ballot order is shuffled with a seed derived from the event and
the voter's key, so it is random across voters and stable across reloads for
one voter.

### Reading unpublished results

*Attack:* fetch `/api/events/<slug>/results` or the tally during the window.

*Answer:* 403 for everyone but organizers of that event until
`results_published_at` is set, in both the API and the pages. The ballot page
never renders totals.

### A judge reads another judge's scores

*Attack:* call `/api/judges/<other>/scores`, or open
`/judge/<event>/review/<their assignment id>/`.

*Answer:* both resolve through `assignments_for_judge(request.user)`; the
row is filtered by judge and by the judge's tracks before anything is read, so
the answer is 403, not a hidden button. The checker's peer-scores probe
exercises exactly this path, and `tests/test_acceptance.py` covers the
fixture-id, user-id and username forms of the URL, plus the UI.

This holds whether or not the caller names an event, and for a judge whose
tracks were narrowed after the assignment was made: the track is checked
when the review is read and again when a score is saved. Someone with no
organizer role gets the same 403 for a judge that exists and one that does
not, so the endpoint cannot be used to list usernames.

### Taking over an invited judge's account

*Attack:* anyone may create an event. Create one, add the email of a judge
who was invited to a different event and has not signed in yet, and ask for
their sign-in link.

*Answer:* an organizer can create a sign-in link only for an account that
has never been used (no password, never signed in, no API token, no staff
rights) **and** whose every role and team is in an event that organizer
runs. The judge invited elsewhere fails the second test, and the link their
real organizer made keeps working. Administrators can create a link for
anyone. The link is shown once, in the response that created it; it is not
stored in the session, and only its hash is in the database.

*Not stopped:* the portal sends no mail, so it never proves that an email
address belongs to the person using it. Whoever invites an address first
decides who receives the link. An organizer who adds a judge that shows as
*Active* without having invited them should confirm with that person that
the account is theirs.

### A judge scores their own team

*Answer:* automatic and manual assignment refuse a judge who is a member of
the project's team.

### Judge collusion or a judge who marks everything the same

*Answer:* not preventable by software. It is made **visible**: the calibration
table shows each judge's mean and spread, flat judges are labelled and made
rank-neutral, and the proof document shows which judges reviewed which
project. Organizers can remove a not-yet-submitted assignment and reassign.

### Deadline gaming

*Attack:* submit at the last second and keep editing; or edit after the
deadline through the API.

*Answer:* the deadline is checked in the service layer on every create,
update, submit and team join, with the server clock. The checker's closed-event
probe is one instance; `tests/test_lifecycle.py` moves the deadline into the
past and checks that the API, the UI form and the join link all refuse. An
organizer extending the deadline is an audited change to the event.

### Submission scraping

*Answer:* the gallery is public by design (T1 requires it); drafts and
hidden projects are not. Member emails appear only in organizer exports.
There is no bulk endpoint without authentication beyond what the gallery
shows.

### Comment spam and abuse

*Answer:* comments require an account, are limited to 10 per minute per
address, exact repeats are rejected, and organizers hide (not delete) with the
actor logged.

### Token theft

*Answer:* tokens are shown once and stored hashed; they can be revoked in
the UI; every API write is audited with the actor. A leaked organizer token
is as bad as a leaked organizer password, so the console has the same
revoke list a password reset would.

### Webhooks pointed at the server's own network

*Attack:* an organizer (on a shared installation, anyone can be one)
registers `http://127.0.0.1:5432/` or a cloud metadata address as a webhook
and reads the result of the ping.

*Answer:* loopback, private, link-local and other non-public addresses are
refused when the webhook is created, in every spelling we know of
(`localhost.`, `127.1`, `2130706433`, IPv4-mapped IPv6). The name is resolved
and checked again each time a delivery is sent, redirects are not followed,
and addresses carrying a username or password are refused. An operator whose
receivers really are on a private network sets
`PLUMBLINE_WEBHOOK_ALLOW_PRIVATE=1`.

*Not stopped:* a name that changes its answer between our check and the
connection a few milliseconds later. Closing that needs the connection
itself pinned to the checked address; if your threat model includes it,
restrict outbound traffic from the container.

### Forged client addresses

*Attack:* send a different `X-Forwarded-For` with every request, so each one
looks like a new visitor to the rate limiter and the duplicate-ballot flags.

*Answer:* the header is ignored unless `PLUMBLINE_TRUST_PROXY=1`. Whatever
the source, an address that does not parse is stored as empty, never as
text.

### The fixture loader on a live installation

*Attack:* not an attacker: the container loads the sample fixtures on every
start. If the loader wrote each time, a restart would undo edited scores,
bring back a deleted administrator with a published password, and promote
whoever had since registered `admin@example.org`.

*Answer:* the loader writes only while the fixture event does not exist. It
never changes an account that is already there. Set `PLUMBLINE_SEED=0` for a
real event.

### A forged or altered certificate

*Attack:* edit a certificate to say first place, or make one up.

*Answer:* every record is a JSON document with a number and an Ed25519
signature. The private key is made from the installation's secret and never
leaves it; the public key is printed in every record and published at
`/verify/key.json`. Anyone can check the signature on their own machine with
`tools/verify_record.py`, which needs nothing but Python. The portal's check
(`/verify/`, `POST /api/records/check`) answers *genuine* only if the
signature fits **and** the record exists here **and** the stored document
is the same. One changed character, a key written twice, a well-signed
document that was never issued: none of them is genuine. A record of a
place stands only while the results are published, the project is in them
at the place and score the record states, and the person is still on the
team. Hide the project, unpublish, or recompute to a different order, and
the record says it does not stand and shows nothing of what it said. That
holds for a withdrawn record too. A statement an organizer withdrew by hand
is not issued again by the next run.

*Not stopped:* someone showing another person's genuine certificate as
their own. The check says the record is real, not who is holding it.
A signature checked offline says the record was issued and is unchanged; it
cannot say the record was withdrawn afterwards. That answer is the
portal's. And a verifier who takes the public key from the record itself,
instead of from the issuer, has checked nothing: a forger prints their own
key. The tool says so when it is used that way.

### A spreadsheet that runs what a team typed

*Attack:* name a team `=HYPERLINK("http://evil.example","click")` and wait
for the organizer to open the export.

*Answer:* every export goes through one function that writes any cell
starting with `=`, `+`, `-`, `@`, tab or carriage return as text, numbers
excepted. The cell is read the way a spreadsheet reads it: spaces and
invisible characters in front are skipped and full-width signs count as the
plain ones. Cells that hold several values are made safe value by value.

*The price:* a phone number written `+82-10-...`, a handle written `@name`
and a comment that opens with a dash carry a leading apostrophe in the
file.

### An import that puts people where they did not ask to be

*Attack:* an organizer, or someone with an organizer's token, imports a
file whose members column lists a judge of the same event, or an
administrator.

*Answer:* the check refuses judges and organizers of the event and staff
accounts, names every existing account it would add, and the import does
nothing if any row is refused.

### A judge who picks their own comparisons

*Attack:* in pairwise mode, answer only the pairs that help a favourite, or
keep answering after the others have stopped.

*Answer:* the portal decides which pair comes next and how many are asked.
An answer about any other pair, or one more than was asked for, is refused.
The next pair depends only on that judge's own answers, so it cannot be
steered by timing either. A judge who joined a team after being assigned
can neither score nor compare that team's project.

### A forged Host header on a printed address

*Attack:* request a certificate with `Host: evil.example` so that the
address printed on it points elsewhere.

*Answer:* with `PLUMBLINE_SITE_URL` set, every printed address is built
from it and the header is ignored. The value is checked at start: anything
but an http or https address stops the portal with a message.

*Not stopped:* with `PLUMBLINE_SITE_URL` empty and `ALLOWED_HOSTS=*`, which
is the sample installation, the address follows the request. Only the
person who sent the header sees that page. Set both for a real event.

### CSRF against the API from a logged-in browser

*Answer:* session-authenticated unsafe API calls must carry Django's CSRF
token; bearer calls are exempt because a cross-site form cannot set an
`Authorization` header. Covered by `test_session_api_write_requires_csrf`.

## Things we deliberately did not build

- **CAPTCHA or proof-of-work on the ballot.** They would need an external
  service or a JavaScript build and would still not stop a determined
  attacker; the mitigation that works is choosing a stricter access mode.
- **Automatic rejection of flagged ballots.** False positives on shared
  networks would silently disenfranchise real voters. Flag, show, let a
  person decide, log the decision.
- **Vote deletion.** A voided ballot keeps its rows; an organizer can always
  reconstruct what happened.
