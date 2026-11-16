from __future__ import annotations

import unittest

from ballot_recognition.vision_model import VISION_SYSTEM_PROMPT, normalize_vision_result


class VisionResultNormalizationTest(unittest.TestCase):
    def test_single_option_unmarked_row_counts_as_negative_vote(self) -> None:
        raw = {
            "title": "表决票",
            "options": [{"code": "agree", "label": "同意"}],
            "candidates": [
                {
                    "row_index": 1,
                    "name": "张三",
                    "checked_options": ["agree"],
                    "selected_option": "agree",
                    "selected_option_label": "同意",
                    "abnormal": False,
                    "abnormal_reason": "",
                },
                {
                    "row_index": 2,
                    "name": "李四",
                    "checked_options": [],
                    "selected_option": None,
                    "selected_option_label": None,
                    "abnormal": False,
                    "abnormal_reason": "",
                },
            ],
        }

        result = normalize_vision_result(raw, "sample.png")

        self.assertFalse(result.is_abnormal)
        self.assertEqual(result.summary, {"agree": 1, "not_checked": 1})
        self.assertEqual(result.option_labels["not_checked"], "不同意")
        self.assertEqual(result.rows[1].selected_option, "not_checked")
        self.assertIsNone(result.rows[1].issue)

    def test_multi_option_unmarked_row_is_missing_mark_abnormal(self) -> None:
        raw = {
            "title": "测评票",
            "options": [
                {"code": "excellent", "label": "优秀"},
                {"code": "qualified", "label": "合格"},
            ],
            "candidates": [
                {
                    "row_index": 1,
                    "name": "王五",
                    "checked_options": [],
                    "selected_option": None,
                    "selected_option_label": None,
                    "abnormal": False,
                    "abnormal_reason": "",
                }
            ],
        }

        result = normalize_vision_result(raw, "sample.png")

        self.assertTrue(result.is_abnormal)
        self.assertEqual(result.summary, {"excellent": 0, "qualified": 0})
        self.assertEqual(result.abnormal_types, ["blank_ballot", "missing_marks"])
        self.assertEqual(result.rows[0].issue, "missing_mark")

    def test_multiple_marked_options_are_abnormal_and_not_counted(self) -> None:
        raw = {
            "title": "测评票",
            "options": [
                {"code": "excellent", "label": "优秀"},
                {"code": "qualified", "label": "合格"},
            ],
            "candidates": [
                {
                    "row_index": 1,
                    "name": "赵六",
                    "checked_options": ["excellent", "qualified"],
                    "selected_option": None,
                    "selected_option_label": None,
                    "abnormal": False,
                    "abnormal_reason": "",
                }
            ],
        }

        result = normalize_vision_result(raw, "sample.png")

        self.assertTrue(result.is_abnormal)
        self.assertEqual(result.summary, {"excellent": 0, "qualified": 0})
        self.assertEqual(result.rows[0].issue, "multiple_marks")
        self.assertIn("multiple_marks", result.abnormal_types)

    def test_invalid_mark_in_single_option_ballot_is_not_counted_as_vote_or_negative(self) -> None:
        raw = {
            "title": "表决票",
            "options": [{"code": "agree", "label": "同意"}],
            "candidates": [
                {
                    "row_index": 1,
                    "name": "钱七",
                    "checked_options": [],
                    "selected_option": None,
                    "selected_option_label": None,
                    "abnormal": True,
                    "abnormal_reason": "invalid_mark",
                }
            ],
        }

        result = normalize_vision_result(raw, "sample.png")

        self.assertTrue(result.is_abnormal)
        self.assertEqual(result.summary, {"agree": 0, "not_checked": 0})
        self.assertEqual(result.rows[0].selected_option, None)
        self.assertEqual(result.rows[0].issue, "invalid_mark")
        self.assertIn("invalid_mark", result.abnormal_types)

    def test_unclear_mark_in_single_option_empty_cell_counts_as_negative_vote(self) -> None:
        raw = {
            "title": "",
            "options": [{"code": "agree_to_check", "label": "同意请打勾"}],
            "candidates": [
                {
                    "row_index": 1,
                    "name": "张三",
                    "marks": [
                        {
                            "option": "agree_to_check",
                            "option_label": "同意请打勾",
                            "mark_type": "unclear",
                            "is_valid_check": False,
                        }
                    ],
                    "checked_options": [],
                    "selected_option": None,
                    "selected_option_label": None,
                    "abnormal": True,
                    "abnormal_reason": "missing_mark",
                },
                {
                    "row_index": 2,
                    "name": "李四",
                    "marks": [
                        {
                            "option": "agree_to_check",
                            "option_label": "同意请打勾",
                            "mark_type": "check_mark",
                            "is_valid_check": True,
                        }
                    ],
                    "checked_options": ["agree_to_check"],
                    "selected_option": "agree_to_check",
                    "selected_option_label": "同意请打勾",
                    "abnormal": False,
                    "abnormal_reason": "",
                },
            ],
        }

        result = normalize_vision_result(raw, "sample.png")

        self.assertFalse(result.is_abnormal)
        self.assertEqual(result.summary, {"agree_to_check": 1, "not_checked": 1})
        self.assertEqual(result.option_labels["not_checked"], "不同意")
        self.assertEqual(result.rows[0].selected_option, "not_checked")
        self.assertIsNone(result.rows[0].issue)
        self.assertEqual(result.rows[1].selected_option, "agree_to_check")

    def test_invalid_mark_type_overrides_model_selected_option(self) -> None:
        for mark_type in ("circle", "x_mark", "filled_block"):
            with self.subTest(mark_type=mark_type):
                raw = {
                    "title": "ballot",
                    "options": [
                        {"code": "excellent", "label": "excellent"},
                        {"code": "qualified", "label": "qualified"},
                    ],
                    "candidates": [
                        {
                            "row_index": 1,
                            "name": "candidate-a",
                            "marks": [
                                {
                                    "option": "excellent",
                                    "option_label": "excellent",
                                    "mark_type": mark_type,
                                    "is_valid_check": False,
                                }
                            ],
                            "checked_options": ["excellent"],
                            "selected_option": "excellent",
                            "selected_option_label": "excellent",
                            "abnormal": False,
                            "abnormal_reason": "",
                        }
                    ],
                }

                result = normalize_vision_result(raw, "sample.png")

                self.assertTrue(result.is_abnormal)
                self.assertEqual(result.summary, {"excellent": 0, "qualified": 0})
                self.assertIsNone(result.rows[0].selected_option)
                self.assertEqual(result.rows[0].issue, "invalid_mark")
                self.assertIn("invalid_mark", result.abnormal_types)

    def test_valid_mark_type_can_supply_checked_options(self) -> None:
        raw = {
            "title": "ballot",
            "options": [
                {"code": "excellent", "label": "excellent"},
                {"code": "qualified", "label": "qualified"},
            ],
            "candidates": [
                {
                    "row_index": 1,
                    "name": "candidate-a",
                    "marks": [
                        {
                            "option": "excellent",
                            "option_label": "excellent",
                            "mark_type": "tick",
                            "is_valid_check": True,
                        }
                    ],
                    "checked_options": [],
                    "selected_option": None,
                    "selected_option_label": None,
                    "abnormal": False,
                    "abnormal_reason": "",
                }
            ],
        }

        result = normalize_vision_result(raw, "sample.png")

        self.assertFalse(result.is_abnormal)
        self.assertEqual(result.summary, {"excellent": 1, "qualified": 0})
        self.assertEqual(result.rows[0].selected_option, "excellent")
        self.assertIsNone(result.rows[0].issue)

    def test_prompt_rejects_circle_x_mark_and_filled_block_as_invalid_marks(self) -> None:
        prompt = VISION_SYSTEM_PROMPT.lower()

        self.assertIn("circle", prompt)
        self.assertIn("x mark", prompt)
        self.assertIn("filled block", prompt)
        self.assertIn("marks", prompt)
        self.assertIn("mark_type", prompt)
        self.assertIn("is_valid_check", prompt)
        self.assertIn("invalid_mark", prompt)
        self.assertIn("do not count", prompt)

    def test_prompt_keeps_current_backend_json_contract_and_strict_judgement_rules(self) -> None:
        prompt = VISION_SYSTEM_PROMPT.lower()

        self.assertIn('"title"', prompt)
        self.assertIn('"options": [{"code": "string", "label": "string"}]', prompt)
        self.assertIn('"candidates"', prompt)
        self.assertIn('"row_index"', prompt)
        self.assertIn('"name"', prompt)
        self.assertNotIn('"ballot_title"', prompt)
        self.assertNotIn('"rows"', prompt)
        self.assertIn("do not infer", prompt)
        self.assertIn("visible information only", prompt)
        self.assertIn("classify every visible mark before deciding", prompt)
        self.assertIn("abnormal_reason must be one of", prompt)
        self.assertIn("unreadable_candidate_name", prompt)


if __name__ == "__main__":
    unittest.main()
