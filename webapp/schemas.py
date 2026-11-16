from __future__ import annotations

from pydantic import BaseModel, Field

from .storage import DEFAULT_ACTIVITY_NAME


class ActivityCreateRequest(BaseModel):
    activity_name: str = Field(default=DEFAULT_ACTIVITY_NAME)
    candidates: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    invalid_mark_rules: list[str] = Field(default_factory=list)


class ActivityDeleteRequest(BaseModel):
    activity_ids: list[str] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    reviewed: bool = False
    comment: str = ""


class RowCorrectionRequest(BaseModel):
    row_index: int
    candidate_name: str = ""
    selected_option: str | None = None
    issue: str | None = None
    comment: str = ""


class BallotCorrectionRequest(BaseModel):
    reviewed: bool = False
    comment: str = ""
    rows: list[RowCorrectionRequest] = Field(default_factory=list)
