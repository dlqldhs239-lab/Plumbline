from __future__ import annotations

from datetime import datetime

from ninja import Schema


class ErrorOut(Schema):
    detail: str


class TrackOut(Schema):
    id: int
    name: str
    external_id: str = ""


class EventOut(Schema):
    id: int
    slug: str
    name: str
    tagline: str = ""
    submissions_open_at: datetime
    submissions_close_at: datetime
    judging_open_at: datetime | None = None
    judging_close_at: datetime | None = None
    results_published_at: datetime | None = None
    phase: str
    tracks: list[TrackOut]


class EventIn(Schema):
    name: str
    slug: str | None = None
    tagline: str = ""
    description: str = ""
    submissions_open_at: datetime
    submissions_close_at: datetime
    judging_open_at: datetime | None = None
    judging_close_at: datetime | None = None
    reviews_per_project: int = 3
    tracks: list[str] = []


class TeamOut(Schema):
    id: int
    name: str
    members: list[str]


class ProjectOut(Schema):
    id: int
    event: str
    team: str
    track: str | None
    title: str
    tagline: str = ""
    description: str = ""
    thumbnail_url: str = ""
    image_urls: list[str] = []
    demo_video_url: str = ""
    repo_url: str = ""
    live_url: str = ""
    tech_tags: list[str] = []
    status: str
    submitted_at: datetime | None = None


class ProjectIn(Schema):
    team_id: int | None = None
    title: str
    tagline: str = ""
    description: str = ""
    thumbnail_url: str = ""
    image_urls: list[str] = []
    demo_video_url: str = ""
    repo_url: str = ""
    live_url: str = ""
    tech_tags: list[str] = []
    track_id: int | None = None
    # Answers to the organizer's own questions, keyed by question id
    # (GET /events/{slug}/questions lists them).
    answers: dict[str, str | bool | None] = {}
    submit: bool = False


class ScoreValues(Schema):
    scores: dict[str, int]
    comment: str | None = None
    submit: bool = False


class AssignmentOut(Schema):
    id: int
    event: str
    project_id: int
    project_title: str
    track: str | None
    batch: str
    status: str
    scores: dict[str, int]
    comment: str = ""
    submitted_at: datetime | None = None


class JudgeScoresOut(Schema):
    judge: str
    event: str | None = None
    assignments: list[AssignmentOut]


class AssignRequest(Schema):
    reviews_per_project: int | None = None
    batch: str | None = None
    seed: int | None = None


class ResultOut(Schema):
    project_id: int
    title: str
    track: str | None
    team: str
    review_count: int
    raw_mean: float | None
    normalized_mean: float | None
    rank_raw: int | None
    rank_normalized: int | None
    adjusted_mean: float | None = None
    rank: int | None = None
    scale_max: int = 5
    criterion_means: list[dict] = []
    community_score: float | None = None
    method: str


class BallotItem(Schema):
    project_id: int
    title: str
    tagline: str = ""
    track: str | None
    team: str
    my_weight: int = 0


class BallotOut(Schema):
    event: str
    quadratic: bool
    credits: int
    credits_used: int
    items: list[BallotItem]


class VoteIn(Schema):
    weight: int = 1


class CommentIn(Schema):
    body: str


class CommentOut(Schema):
    id: int
    author: str
    body: str
    created_at: datetime
    hidden: bool = False


class ProgressRow(Schema):
    judge_id: int
    name: str
    total: int
    submitted: int
    in_progress: int
    pending: int
    pct: int
