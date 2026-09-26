from __future__ import annotations

from datetime import datetime

from ninja import Schema


class EventPatch(Schema):
    name: str | None = None
    tagline: str | None = None
    description: str | None = None
    submissions_open_at: datetime | None = None
    submissions_close_at: datetime | None = None
    judging_open_at: datetime | None = None
    judging_close_at: datetime | None = None
    reviews_per_project: int | None = None
    voting_access: str | None = None
    voting_open_at: datetime | None = None
    voting_close_at: datetime | None = None
    voting_credits: int | None = None
    comments_enabled: bool | None = None
    is_listed: bool | None = None


class JudgeIn(Schema):
    email: str
    name: str = ""
    tracks: list[int] = []


class JudgeOut(Schema):
    id: int
    external_id: str = ""
    username: str
    email: str
    name: str = ""
    tracks: list[str]


class CriterionIn(Schema):
    key: str
    name: str
    weight: float = 1.0
    description: str = ""


class RubricOut(Schema):
    name: str
    scale_min: int
    scale_max: int
    instructions: str = ""
    criteria: list[CriterionIn]


class ManualAssignIn(Schema):
    judge: str
    project_id: int
    batch: str = "manual"


class VoterOut(Schema):
    id: int
    kind: str
    label: str
    votes: int
    flags: list[str]
    voided: bool


class BallotLinksIn(Schema):
    emails: list[str]


class BallotLinkOut(Schema):
    email: str
    url: str


class AuditOut(Schema):
    id: int
    at: datetime
    actor: str = ""
    action: str
    target_type: str = ""
    target_id: str = ""
    target: str = ""
    channel: str = ""
    detail: dict
