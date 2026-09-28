"""The audit log as an organizer reads it.

The log stores an action name and a small JSON detail. That is right for a
program and tiring for a person at the end of an event, who wants to know
what happened. This module turns an entry into a sentence and a short list
of facts. It only reads; what is stored and exported stays as it was.
"""

from __future__ import annotations

# What was done, as the words that follow the actor's name. The name of what
# it was done to comes after, from the entry itself.
PHRASES = {
    "account.signup": "created an account",
    "account.claim": "signed in for the first time",
    "assignment.balanced": "assigned judges automatically",
    "assignment.manual": "assigned by hand",
    "assignment.remove": "took back the assignment",
    "comment.create": "commented on",
    "comment.hide": "hid a comment on",
    "comment.unhide": "showed a comment again on",
    "comparison.submit": "compared two projects",
    "event.create": "created the event",
    "event.update": "changed the settings of",
    "event.tracks.add": "added tracks to",
    "export.csv": "exported a CSV file",
    "import.judges": "imported judges",
    "import.projects": "imported projects",
    "judge.add": "added as judge",
    "judge.invite": "invited",
    "judge.remove": "removed the judge",
    "judge.sign_in_link": "made a sign-in link for",
    "prize.create": "added the prize",
    "prize.update": "changed the prize",
    "prize.delete": "removed the prize",
    "project.create": "started the project",
    "project.update": "edited",
    "project.submit": "submitted",
    "project.withdraw": "withdrew",
    "project.hide": "hid",
    "project.unhide": "showed again",
    "question.create": "added the form question",
    "question.update": "changed the form question",
    "question.delete": "removed the form question",
    "records.issue": "issued the records",
    "records.revoke": "withdrew the record",
    "results.recompute": "computed the results",
    "results.publish": "published the results",
    "results.unpublish": "took the results back",
    "rubric.replace": "set the criteria of the",
    "rubric.jury_k": "changed the jury-size adjustment",
    "rubric.pairwise": "switched pairwise comparisons",
    "score.save": "saved a draft review of",
    "score.submit": "submitted a review of",
    "seed.fixtures": "loaded the fixture set",
    "seed.open_house": "loaded the Open House event",
    "team.create": "formed the team",
    "team.join": "joined the team",
    "team.invite.create": "made an invitation to",
    "token.issue": "issued an API token",
    "token.revoke": "revoked an API token",
    "vote.cast": "voted",
    "vote.remove": "took back a vote",
    "voter.admit": "opened a ballot",
    "voter.issue_links": "made ballot links",
    "voter.void": "voided the ballot",
    "webhook.create": "added the webhook",
    "webhook.delete": "removed the webhook",
    "webhook.enable": "switched on the webhook",
    "webhook.disable": "switched off the webhook",
}

# Actions whose target is already named by the phrase, or is an internal row
# whose label would mean nothing to a reader.
NO_TARGET = {
    "assignment.balanced",
    "comparison.submit",
    "export.csv",
    "records.issue",
    "results.recompute",
    "results.publish",
    "results.unpublish",
    "rubric.jury_k",
    "rubric.pairwise",
    "seed.fixtures",
    "seed.open_house",
    "event.create",
    "voter.admit",
    "voter.void",
    "vote.cast",
    "vote.remove",
}

LABELS = {
    "jury_k": "jury size",
    "judge_k": "judge weight",
    "panel_mean": "panel mean",
    "panel_stdev": "panel spread",
    "has_comment": "with feedback",
    "lookalikes": "same address as",
    "reviews_submitted": "reviews submitted",
    "answers_removed": "answers removed",
    "held_back": "held back",
    "unplaced_projects": "not fully covered",
}
CHANNELS = {
    "ui": "in the browser",
    "api": "over the API",
    "seed": "at first start",
    "system": "by the portal",
    "cli": "from the command line",
}


def phrase(action: str) -> str:
    if action in PHRASES:
        return PHRASES[action]
    return action.replace(".", " ").replace("_", " ")


def _short(value) -> str:
    if value is None or value == "":
        return "none"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, dict):
        return ", ".join(f"{_label(k)} {_short(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_short(v) for v in value) if value else "none"
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."


def _label(key) -> str:
    key = str(key)
    return LABELS.get(key, key.replace("_", " "))


def facts(entry) -> list[dict]:
    """The detail as label and value pairs. A change is shown as what it was
    and what it became."""
    detail = entry.detail
    if not isinstance(detail, dict):
        return [{"label": "detail", "value": _short(detail)}] if detail else []
    out = []
    for key, value in detail.items():
        if key == "changed" and isinstance(value, dict):
            for field, pair in value.items():
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    out.append({"label": _label(field), "was": _short(pair[0]), "value": _short(pair[1])})
                else:
                    out.append({"label": _label(field), "value": _short(pair)})
        elif key == "changed" and isinstance(value, (list, tuple)) and len(value) == 2:
            out.append({"label": "value", "was": _short(value[0]), "value": _short(value[1])})
        elif key == "enabled":
            out.append({"label": "now", "value": "on" if value else "off"})
        elif key == "criteria" and isinstance(value, list):
            for c in value:
                if isinstance(c, dict):
                    out.append(
                        {"label": str(c.get("name") or c.get("key")), "value": f"weight {_short(c.get('weight'))}"}
                    )
        else:
            out.append({"label": _label(key), "value": _short(value)})
    return out[:12]


def _comparison(entry, titles: dict) -> tuple[str, list] | None:
    """A comparison is stored as three project numbers. Said in words."""
    d = entry.detail if isinstance(entry.detail, dict) else {}
    left, right, preferred = d.get("left"), d.get("right"), d.get("preferred")
    if left not in titles or right not in titles:
        return None
    if preferred is None:
        return f"could not choose between {titles[left]} and {titles[right]}", []
    other = right if preferred == left else left
    return f"preferred {titles.get(preferred, preferred)} to {titles[other]}", []


def read(entry, titles: dict | None = None) -> dict:
    """One entry, ready for the page."""
    who = (entry.actor.get_full_name() or entry.actor.get_username()) if entry.actor_id and entry.actor else ""
    who = who or entry.actor_label
    if not who:
        # Nobody was signed in. In the browser or over the API that is a
        # visitor; anywhere else it is the portal itself.
        who = "A visitor" if entry.channel in ("ui", "api") else "The portal"
    target = "" if entry.action in NO_TARGET else (entry.target_label or "")
    did, what = phrase(entry.action), facts(entry)
    if entry.action == "comparison.submit":
        said = _comparison(entry, titles or {})
        if said:
            did, what = said
    return {
        "entry": entry,
        "who": who,
        "did": did,
        "target": target,
        "facts": what,
        "via": CHANNELS.get(entry.channel, entry.channel),
    }


def by_day(entries) -> list[dict]:
    """Entries under the day they happened on, in the order given."""
    entries = list(entries)
    wanted = set()
    for e in entries:
        if e.action == "comparison.submit" and isinstance(e.detail, dict):
            wanted.update(v for v in (e.detail.get("left"), e.detail.get("right")) if isinstance(v, int))
    titles = {}
    if wanted:
        from events.models import Project

        titles = dict(Project.objects.filter(pk__in=wanted).values_list("id", "title"))
    days: list[dict] = []
    for e in entries:
        day = e.created_at.date()
        if not days or days[-1]["day"] != day:
            days.append({"day": day, "rows": []})
        days[-1]["rows"].append(read(e, titles))
    return days
