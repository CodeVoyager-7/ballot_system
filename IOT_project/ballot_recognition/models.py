from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RowRecognition:
    row_index: int
    candidate_name: str
    recognized_name: str
    selected_option: str | None
    option_scores: dict[str, float]
    is_ambiguous: bool
    issue: str | None
    row_bbox: list[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BallotRecognitionResult:
    template_name: str
    image_path: str
    summary: dict[str, int]
    rows: list[RowRecognition]
    is_abnormal: bool = False
    abnormal_types: list[str] = field(default_factory=list)
    abnormal_details: dict[str, Any] = field(default_factory=dict)
    recognition_source: str = "vision_model"
    title: str = ""
    option_labels: dict[str, str] = field(default_factory=dict)
    raw_model_output: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "image_path": self.image_path,
            "summary": self.summary,
            "rows": [row.to_dict() for row in self.rows],
            "is_abnormal": self.is_abnormal,
            "abnormal_types": self.abnormal_types,
            "abnormal_details": self.abnormal_details,
            "recognition_source": self.recognition_source,
            "title": self.title,
            "option_labels": self.option_labels,
            "raw_model_output": self.raw_model_output,
        }


@dataclass(slots=True)
class BatchBallotRecognitionResult:
    template_name: str
    ballot_count: int
    processed_images: list[str]
    option_totals: dict[str, int]
    candidate_totals: dict[str, dict[str, int]]
    ballots: list[BallotRecognitionResult]
    abnormal_ballot_count: int
    abnormal_type_totals: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_name": self.template_name,
            "ballot_count": self.ballot_count,
            "processed_images": self.processed_images,
            "option_totals": self.option_totals,
            "candidate_totals": self.candidate_totals,
            "ballots": [ballot.to_dict() for ballot in self.ballots],
            "abnormal_ballot_count": self.abnormal_ballot_count,
            "abnormal_type_totals": self.abnormal_type_totals,
        }
