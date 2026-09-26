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
