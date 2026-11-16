from __future__ import annotations

import io
import tempfile
import time
import unittest
from pathlib import Path
from zipfile import ZipFile

import cv2
import numpy as np

from ballot_recognition.models import BallotRecognitionResult, BatchBallotRecognitionResult, RowRecognition
import webapp.storage as storage
import webapp.tasks as tasks


class WebAppFeatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self._old_paths = (
            storage.DATA_DIR,
            storage.STATE_PATH,
            storage.ACTIVITIES_DIR,
            storage.LEGACY_UPLOADS_DIR,
            storage.LEGACY_RESULTS_DIR,
        )
        storage.DATA_DIR = self.data_dir
        storage.STATE_PATH = self.data_dir / "state.json"
        storage.ACTIVITIES_DIR = self.data_dir / "activities"
        storage.LEGACY_UPLOADS_DIR = self.data_dir / "uploads"
        storage.LEGACY_RESULTS_DIR = self.data_dir / "results"
        storage._ensure_storage()

    def tearDown(self) -> None:
        (
            storage.DATA_DIR,
            storage.STATE_PATH,
            storage.ACTIVITIES_DIR,
            storage.LEGACY_UPLOADS_DIR,
            storage.LEGACY_RESULTS_DIR,
        ) = self._old_paths
        self.tmp.cleanup()

    def test_activity_template_is_normalized_and_saved_in_state(self) -> None:
        state = {
            "latest_activity_id": "act_1",
            "activities": [
                storage._normalize_activity_meta(
                    {
                        "activity_id": "act_1",
                        "activity_name": "换届投票",
                        "template": {
                            "candidates": ["张三", "李四"],
                            "options": ["同意", "不同意"],
                            "invalid_mark_rules": ["circle", "x_mark", "filled_block"],
                        },
                    }
                )
            ],
        }

        storage._save_state(state)
        loaded = storage._load_state()

        template = loaded["activities"][0]["template"]
        self.assertEqual(template["candidates"], ["张三", "李四"])
        self.assertEqual(template["options"], ["同意", "不同意"])
        self.assertIn("filled_block", template["invalid_mark_rules"])

    def test_activity_templates_are_saved_for_reuse_and_deduplicated(self) -> None:
        state = {"latest_activity_id": None, "activities": [], "activity_templates": []}
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_1",
                "activity_name": "2026 spring review",
                "template": {
                    "candidates": ["Alice", "Bob"],
                    "options": ["Excellent", "Qualified"],
                    "invalid_mark_rules": ["circle", "x_mark"],
                },
            }
        )

        storage._remember_activity_template(state, meta)
        storage._remember_activity_template(state, meta)
        storage._save_state(state)
        loaded = storage._load_state()

        self.assertEqual(len(loaded["activity_templates"]), 1)
        saved_template = loaded["activity_templates"][0]
        self.assertEqual(saved_template["activity_name"], "2026 spring review")
        self.assertEqual(saved_template["template"]["candidates"], ["Alice", "Bob"])
        self.assertEqual(saved_template["template"]["options"], ["Excellent", "Qualified"])
        self.assertEqual(saved_template["template"]["invalid_mark_rules"], ["circle", "x_mark"])

        overview = storage._build_overview()
        self.assertEqual(overview["activity_templates"][0]["template_id"], saved_template["template_id"])

    def test_async_mobile_upload_task_refreshes_activity_totals(self) -> None:
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_mobile",
                "activity_name": "手机上传测试",
                "template": {"candidates": ["张三"], "options": ["同意", "不同意"]},
            }
        )
        storage._save_state({"latest_activity_id": "act_mobile", "activities": [meta], "activity_templates": []})
        upload_dir = storage._upload_dir("act_mobile", "upl_mobile")
        upload_dir.mkdir(parents=True)
        image_path = upload_dir / "001_mobile.jpg"
        image_path.write_bytes(b"fake image bytes")

        class FakeRecognizer:
            def recognize_directory(self, input_dir: str | Path) -> BatchBallotRecognitionResult:
                return BatchBallotRecognitionResult(
                    template_name="vision_model",
                    ballot_count=1,
                    processed_images=[str(image_path)],
                    option_totals={"同意": 1, "不同意": 0},
                    candidate_totals={"张三": {"同意": 1, "不同意": 0}},
                    ballots=[
                        BallotRecognitionResult(
                            template_name="vision_model",
                            image_path=str(image_path),
                            summary={"agree": 1, "disagree": 0},
                            option_labels={"agree": "同意", "disagree": "不同意"},
                            rows=[
                                RowRecognition(
                                    row_index=1,
                                    candidate_name="张三",
                                    recognized_name="张三",
                                    selected_option="agree",
                                    option_scores={},
                                    is_ambiguous=False,
                                    issue=None,
                                    row_bbox=[],
                                )
                            ],
                            is_abnormal=False,
                            abnormal_types=[],
                            abnormal_details={"valid_vote_count": 1},
                        )
                    ],
                    abnormal_ballot_count=0,
                    abnormal_type_totals={},
                )

        old_recognizer = storage.recognizer
        storage.recognizer = FakeRecognizer()
        try:
            task_id = tasks._start_upload_task("act_mobile", "upl_mobile")
            deadline = time.time() + 5
            while time.time() < deadline:
                with tasks.TASK_LOCK:
                    task = tasks.TASKS.get(task_id, {})
                if task.get("status") != "running":
                    break
                time.sleep(0.05)

            with tasks.TASK_LOCK:
                task = tasks.TASKS[task_id]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["result"]["ballot_count"], 1)
            self.assertEqual(task["result"]["option_totals"], {"同意": 1, "不同意": 0})

            overview = storage._build_overview()
            self.assertEqual(overview["ballot_count"], 1)
            self.assertEqual(overview["current_activity"]["candidate_totals"]["张三"], {"同意": 1, "不同意": 0})
        finally:
            storage.recognizer = old_recognizer

    def test_single_option_template_counts_positive_alias_from_mobile_result(self) -> None:
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_single_alias",
                "activity_name": "single option alias",
                "template": {"candidates": ["Alice", "Bob"], "options": ["Agree if checked"]},
            }
        )
        storage._save_state({"latest_activity_id": "act_single_alias", "activities": [meta], "activity_templates": []})
        upload_dir = storage._upload_dir("act_single_alias", "upl_mobile_alias")
        upload_dir.mkdir(parents=True)
        result_path = upload_dir / "result.json"
        result_path.write_text(
            storage.json.dumps(
                {
                    "upload_id": "upl_mobile_alias",
                    "activity_id": "act_single_alias",
                    "created_at": "2026-06-03T12:00:00",
                    "ballots": [
                        {
                            "ballot_id": "upl_mobile_alias_001",
                            "file_name": "mobile.jpg",
                            "summary": {"agree": 1, "not_checked": 0},
                            "option_labels": {"agree": "Agree", "not_checked": "Not agreed"},
                            "rows": [
                                {
                                    "row_index": 1,
                                    "candidate_name": "Alice",
                                    "recognized_name": "Alice",
                                    "selected_option": "not_checked",
                                    "issue": None,
                                },
                                {
                                    "row_index": 2,
                                    "candidate_name": "Bob",
                                    "recognized_name": "Bob",
                                    "selected_option": "agree",
                                    "issue": None,
                                },
                            ],
                            "is_abnormal": False,
                            "abnormal_types": [],
                            "abnormal_details": {"valid_vote_count": 1},
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        detail = storage._build_activity_detail(meta)

        self.assertEqual(detail["option_totals"], {"Agree if checked": 1})
        self.assertEqual(detail["candidate_totals"]["Alice"], {"Agree if checked": 0})
        self.assertEqual(detail["candidate_totals"]["Bob"], {"Agree if checked": 1})

    def test_ballot_correction_recalculates_upload_and_activity_totals(self) -> None:
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_1",
                "activity_name": "换届投票",
                "template": {"candidates": ["张三"], "options": ["同意", "不同意"]},
            }
        )
        storage._save_state({"latest_activity_id": "act_1", "activities": [meta]})
        upload_dir = storage._upload_dir("act_1", "upl_1")
        upload_dir.mkdir(parents=True)
        result_path = upload_dir / "result.json"
        result_path.write_text(
            storage.json.dumps(
                {
                    "upload_id": "upl_1",
                    "activity_id": "act_1",
                    "created_at": "2026-05-18T10:00:00",
                    "option_totals": {"同意": 1, "不同意": 0},
                    "candidate_totals": {"张三": {"同意": 1, "不同意": 0}},
                    "ballots": [
                        {
                            "ballot_id": "upl_1_001",
                            "file_name": "a.png",
                            "summary": {"agree": 1, "disagree": 0},
                            "option_labels": {"agree": "同意", "disagree": "不同意"},
                            "rows": [
                                {
                                    "row_index": 1,
                                    "candidate_name": "张三",
                                    "recognized_name": "张三",
                                    "selected_option": "agree",
                                    "option_scores": {},
                                    "is_ambiguous": False,
                                    "issue": None,
                                    "row_bbox": [],
                                }
                            ],
                            "is_abnormal": False,
                            "abnormal_types": [],
                            "abnormal_details": {"valid_vote_count": 1},
                        }
                    ],
                    "abnormal_ballot_count": 0,
                    "abnormal_type_totals": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        result = storage._update_ballot_correction(
            "act_1",
            "upl_1_001",
            [
                {
                    "row_index": 1,
                    "candidate_name": "张三",
                    "selected_option": None,
                    "issue": "invalid_mark",
                    "comment": "画叉，按投票错误处理",
                }
            ],
            reviewed=True,
            comment="已人工确认",
        )

        ballot = result["ballot"]
        self.assertTrue(ballot["is_abnormal"])
        self.assertEqual(ballot["summary"], {"同意": 0, "不同意": 0})
        self.assertEqual(ballot["rows"][0]["issue"], "invalid_mark")
        self.assertEqual(result["activity"]["option_totals"], {"同意": 0, "不同意": 0})
        self.assertEqual(result["activity"]["candidate_totals"]["张三"], {"同意": 0, "不同意": 0})
        self.assertEqual(result["activity"]["abnormal_ballot_count"], 1)

        fixed = storage._update_ballot_correction(
            "act_1",
            "upl_1_001",
            [
                {
                    "row_index": 1,
                    "candidate_name": "张三",
                    "selected_option": "同意",
                    "issue": None,
                    "comment": "改回有效勾选",
                }
            ],
            reviewed=True,
            comment="已二次确认",
        )

        self.assertFalse(fixed["ballot"]["is_abnormal"])
        self.assertEqual(fixed["ballot"]["abnormal_types"], [])
        self.assertEqual(fixed["activity"]["option_totals"], {"同意": 1, "不同意": 0})

    def test_csv_and_xlsx_exports_include_corrected_results(self) -> None:
        activity = {
            "activity_id": "act_1",
            "activity_name": "换届投票",
            "ballot_count": 1,
            "abnormal_ballot_count": 1,
            "option_totals": {"同意": 0, "不同意": 0},
            "candidate_totals": {"张三": {"同意": 0, "不同意": 0}},
            "abnormal_type_totals": {"invalid_mark": 1},
            "ballots": [
                {
                    "ballot_id": "upl_1_001",
                    "file_name": "a.png",
                    "upload_id": "upl_1",
                    "is_abnormal": True,
                    "abnormal_types": ["invalid_mark"],
                    "summary": {"同意": 0, "不同意": 0},
                    "rows": [
                        {
                            "row_index": 1,
                            "candidate_name": "张三",
                            "selected_option": None,
                            "issue": "invalid_mark",
                        }
                    ],
                    "review": {"reviewed": True, "comment": "已人工确认"},
                }
            ],
        }

        csv_text = storage._build_activity_csv_export(activity)
        xlsx_bytes = storage._build_activity_xlsx_export(activity)

        self.assertIn("活动总览", csv_text)
        self.assertIn("异常票清单", csv_text)
        self.assertIn("invalid_mark", csv_text)
        with ZipFile(io.BytesIO(xlsx_bytes)) as workbook:
            names = workbook.namelist()
        self.assertIn("xl/workbook.xml", names)
        self.assertIn("xl/worksheets/sheet1.xml", names)

    def test_activity_detail_flags_rephotographed_duplicate_ballots(self) -> None:
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_1",
                "activity_name": "duplicate test",
                "template": {"candidates": ["Alice"], "options": ["Agree", "Disagree"]},
            }
        )
        storage._save_state({"latest_activity_id": "act_1", "activities": [meta]})
        upload_dir = storage._upload_dir("act_1", "upl_1")
        upload_dir.mkdir(parents=True)

        base_image = np.full((120, 180, 3), 245, dtype=np.uint8)
        cv2.rectangle(base_image, (20, 20), (160, 100), (20, 20, 20), 2)
        cv2.line(base_image, (65, 65), (78, 80), (20, 20, 20), 3)
        cv2.line(base_image, (78, 80), (110, 42), (20, 20, 20), 3)
        brighter_image = np.clip(base_image.astype(np.int16) + 8, 0, 255).astype(np.uint8)
        cv2.imwrite(str(upload_dir / "001_a.png"), base_image)
        cv2.imwrite(str(upload_dir / "002_b.png"), brighter_image)

        ballot_rows = [
            {
                "row_index": 1,
                "candidate_name": "Alice",
                "recognized_name": "Alice",
                "selected_option": "agree",
                "option_scores": {},
                "is_ambiguous": False,
                "issue": None,
                "row_bbox": [],
            }
        ]
        result_path = upload_dir / "result.json"
        result_path.write_text(
            storage.json.dumps(
                {
                    "upload_id": "upl_1",
                    "activity_id": "act_1",
                    "created_at": "2026-05-18T10:00:00",
                    "ballots": [
                        {
                            "ballot_id": "upl_1_001",
                            "file_name": "001_a.png",
                            "image_path": str(upload_dir / "001_a.png"),
                            "summary": {"agree": 1, "disagree": 0},
                            "option_labels": {"agree": "Agree", "disagree": "Disagree"},
                            "rows": ballot_rows,
                            "is_abnormal": False,
                            "abnormal_types": [],
                            "abnormal_details": {"valid_vote_count": 1},
                        },
                        {
                            "ballot_id": "upl_1_002",
                            "file_name": "002_b.png",
                            "image_path": str(upload_dir / "002_b.png"),
                            "summary": {"agree": 1, "disagree": 0},
                            "option_labels": {"agree": "Agree", "disagree": "Disagree"},
                            "rows": ballot_rows,
                            "is_abnormal": False,
                            "abnormal_types": [],
                            "abnormal_details": {"valid_vote_count": 1},
                        },
                    ],
                    "abnormal_ballot_count": 0,
                    "abnormal_type_totals": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        detail = storage._build_activity_detail(meta)

        self.assertEqual(detail["duplicate_ballot_count"], 1)
        self.assertEqual(len(detail["duplicate_groups"]), 1)
        self.assertEqual(detail["duplicate_groups"][0]["kind"], "suspected_same_ballot")
        self.assertEqual({item["ballot_id"] for item in detail["duplicate_groups"][0]["ballots"]}, {"upl_1_001", "upl_1_002"})
        by_id = {ballot["ballot_id"]: ballot for ballot in detail["ballots"]}
        self.assertFalse(by_id["upl_1_001"]["is_duplicate"])
        self.assertTrue(by_id["upl_1_002"]["is_duplicate"])
        self.assertEqual(detail["duplicate_groups"][0]["original_ballot_id"], "upl_1_001")
        self.assertEqual(detail["valid_vote_count"], 2)
        self.assertEqual(detail["reviewed_ballot_count"], 0)
        self.assertEqual(detail["review_completion_rate"], 0)

    def test_delete_duplicate_ballot_removes_it_from_statistics(self) -> None:
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_1",
                "activity_name": "duplicate delete test",
                "template": {"candidates": ["Alice"], "options": ["Agree", "Disagree"]},
            }
        )
        storage._save_state({"latest_activity_id": "act_1", "activities": [meta]})
        upload_dir = storage._upload_dir("act_1", "upl_1")
        upload_dir.mkdir(parents=True)

        for file_name in ("001_a.png", "002_b.png"):
            cv2.imwrite(str(upload_dir / file_name), np.full((40, 60, 3), 245, dtype=np.uint8))

        def ballot(ballot_id: str, file_name: str) -> dict[str, object]:
            return {
                "ballot_id": ballot_id,
                "file_name": file_name,
                "image_path": str(upload_dir / file_name),
                "summary": {"agree": 1, "disagree": 0},
                "option_labels": {"agree": "Agree", "disagree": "Disagree"},
                "rows": [
                    {
                        "row_index": 1,
                        "candidate_name": "Alice",
                        "recognized_name": "Alice",
                        "selected_option": "agree",
                        "option_scores": {},
                        "is_ambiguous": False,
                        "issue": None,
                        "row_bbox": [],
                    }
                ],
                "is_abnormal": False,
                "abnormal_types": [],
                "abnormal_details": {"valid_vote_count": 1},
                "file_hash": "same-file-hash",
            }

        (upload_dir / "result.json").write_text(
            storage.json.dumps(
                {
                    "upload_id": "upl_1",
                    "activity_id": "act_1",
                    "created_at": "2026-05-18T10:00:00",
                    "ballots": [ballot("upl_1_001", "001_a.png"), ballot("upl_1_002", "002_b.png")],
                    "abnormal_ballot_count": 0,
                    "abnormal_type_totals": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        before = storage._build_activity_detail(meta)
        self.assertEqual(before["candidate_totals"]["Alice"]["Agree"], 2)
        self.assertFalse(next(item for item in before["ballots"] if item["ballot_id"] == "upl_1_001")["is_duplicate"])
        self.assertTrue(next(item for item in before["ballots"] if item["ballot_id"] == "upl_1_002")["is_duplicate"])

        result = storage._delete_ballot("act_1", "upl_1_002")

        self.assertEqual(result["deleted_ballot_id"], "upl_1_002")
        self.assertEqual(result["activity"]["ballot_count"], 1)
        self.assertEqual(result["activity"]["candidate_totals"]["Alice"], {"Agree": 1, "Disagree": 0})
        self.assertEqual(result["activity"]["duplicate_ballot_count"], 0)
        self.assertFalse((upload_dir / "002_b.png").exists())

    def test_activity_detail_does_not_group_visually_similar_ballots_with_different_results(self) -> None:
        meta = storage._normalize_activity_meta(
            {
                "activity_id": "act_1",
                "activity_name": "duplicate test",
                "template": {"candidates": ["Alice"], "options": ["Agree", "Disagree"]},
            }
        )
        storage._save_state({"latest_activity_id": "act_1", "activities": [meta]})
        upload_dir = storage._upload_dir("act_1", "upl_1")
        upload_dir.mkdir(parents=True)

        image = np.full((120, 180, 3), 245, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (160, 100), (20, 20, 20), 2)
        brighter_image = np.clip(image.astype(np.int16) + 8, 0, 255).astype(np.uint8)
        cv2.imwrite(str(upload_dir / "001_a.png"), image)
        cv2.imwrite(str(upload_dir / "002_b.png"), brighter_image)

        def ballot(ballot_id: str, file_name: str, selected: str) -> dict[str, object]:
            return {
                "ballot_id": ballot_id,
                "file_name": file_name,
                "image_path": str(upload_dir / file_name),
                "summary": {"agree": 1 if selected == "agree" else 0, "disagree": 1 if selected == "disagree" else 0},
                "option_labels": {"agree": "Agree", "disagree": "Disagree"},
                "rows": [
                    {
                        "row_index": 1,
                        "candidate_name": "Alice",
                        "recognized_name": "Alice",
                        "selected_option": selected,
                        "option_scores": {},
                        "is_ambiguous": False,
                        "issue": None,
                        "row_bbox": [],
                    }
                ],
                "is_abnormal": False,
                "abnormal_types": [],
                "abnormal_details": {"valid_vote_count": 1},
            }

        (upload_dir / "result.json").write_text(
            storage.json.dumps(
                {
                    "upload_id": "upl_1",
                    "activity_id": "act_1",
                    "created_at": "2026-05-18T10:00:00",
                    "ballots": [ballot("upl_1_001", "001_a.png", "agree"), ballot("upl_1_002", "002_b.png", "disagree")],
                    "abnormal_ballot_count": 0,
                    "abnormal_type_totals": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        detail = storage._build_activity_detail(meta)

        self.assertEqual(detail["duplicate_ballot_count"], 0)
        self.assertEqual(detail["duplicate_groups"], [])


if __name__ == "__main__":
    unittest.main()
