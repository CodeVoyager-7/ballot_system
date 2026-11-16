from __future__ import annotations

from pathlib import Path

from .models import BallotRecognitionResult, BatchBallotRecognitionResult
from .vision_model import VisionBallotRecognizer

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


class VisionBatchBallotRecognizer:
    def __init__(self, recognizer: VisionBallotRecognizer | None = None) -> None:
        self.recognizer = recognizer or VisionBallotRecognizer()

    def recognize_directory(self, input_dir: str | Path) -> BatchBallotRecognitionResult:
        input_path = Path(input_dir)
        if not input_path.exists():
            raise FileNotFoundError(f"Input directory not found: {input_path}")
        image_paths = sorted(
            path for path in input_path.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )
        if not image_paths:
            raise FileNotFoundError(f"No supported image files found in: {input_path}")

        ballots: list[BallotRecognitionResult] = []
        option_totals: dict[str, int] = {}
        candidate_totals: dict[str, dict[str, int]] = {}
        abnormal_type_totals: dict[str, int] = {
            "blank_ballot": 0,
            "multiple_marks": 0,
            "missing_marks": 0,
            "recognition_failed": 0,
        }

        for image_path in image_paths:
            ballot = self.recognizer.recognize_with_fallback(image_path)
            ballots.append(ballot)
            for option_code, count in ballot.summary.items():
                option_label = _option_label(ballot, option_code)
                option_totals[option_label] = option_totals.get(option_label, 0) + count
            for row in ballot.rows:
                candidate_totals.setdefault(row.candidate_name, {})
                for option_code in option_totals:
                    candidate_totals[row.candidate_name].setdefault(option_code, 0)
                if row.selected_option is not None:
                    option_label = _option_label(ballot, row.selected_option)
                    candidate_totals[row.candidate_name][option_label] = (
                        candidate_totals[row.candidate_name].get(option_label, 0) + 1
                    )
            for abnormal_type in ballot.abnormal_types:
                abnormal_type_totals[abnormal_type] = abnormal_type_totals.get(abnormal_type, 0) + 1

        for counts in candidate_totals.values():
            for option_code in option_totals:
                counts.setdefault(option_code, 0)

        return BatchBallotRecognitionResult(
            template_name="vision_model",
            ballot_count=len(ballots),
            processed_images=[str(path) for path in image_paths],
            option_totals=option_totals,
            candidate_totals=candidate_totals,
            ballots=ballots,
            abnormal_ballot_count=sum(1 for ballot in ballots if ballot.is_abnormal),
            abnormal_type_totals=abnormal_type_totals,
        )


def _option_label(ballot: BallotRecognitionResult, option_code: str) -> str:
    return str(ballot.option_labels.get(option_code) or option_code)
