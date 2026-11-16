from __future__ import annotations

import base64
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import BallotRecognitionResult, RowRecognition


VISION_SYSTEM_PROMPT = """You are a strict ballot recognition assistant.

Recognize the voting ballot image and return only valid JSON. Base every decision on visible information only.

Tasks:
1. Identify the ballot title if visible.
2. Identify all evaluation options, such as excellent/qualified/basic qualified/unqualified or pass/fail.
3. Identify all candidate names.
4. For each candidate row, list every visible mark inside option cells in marks.
5. For each mark, classify mark_type and whether it is a valid check/tick.
6. For each candidate row, identify exactly which option is validly checked.
7. Mark abnormal rows, including invalid mark, missing mark, multiple marks, unclear mark, or unreadable candidate name.

Rules:
- Use visible information only. Do not infer, guess, complete, translate, or normalize unreadable content beyond what is visible.
- If the title is not visible or cannot be confirmed, return an empty string for title.
- If a candidate name is unreadable, set name to "unreadable", selected_option to null, checked_options to [], abnormal to true, and abnormal_reason to "unreadable_candidate_name".
- Process each candidate row independently. Do not use other rows, prior ballots, common voting patterns, or statistical expectations to decide a row.
- Classify every visible mark before deciding checked_options or selected_option.
- Only a standard check mark or tick inside an option cell counts as a selected option.
- A V-shaped check mark or short tick is valid. Use mark_type "check_mark" or "tick" and is_valid_check true.
- A circle/O mark, x mark, cross, filled block/black square, black dot, scribble, line, or heavy shading is an invalid voting mark. Use mark_type such as "circle", "x_mark", "cross", "filled_block", "black_dot", "scribble", "line", or "heavy_shading" and is_valid_check false.
- Table borders, printed grid lines, row separators, column separators, crop edges, paper shadows, and background texture are not voting marks. Do not include them in marks.
- For an empty option cell, marks must be [].
- Do not output mark_type "unclear" merely because the cell contains table borders, printed lines, faint ruling, crop edges, shadows, or background texture.
- If a row has any invalid voting mark inside an option cell, do not count it as a selected option; set selected_option to null, checked_options to [], abnormal to true, and abnormal_reason to "invalid_mark".
- Ignore marks outside option cells unless they make the row unreadable or make the selected option unclear.
- If a mark touches or crosses multiple option cells and the intended option cannot be determined, selected_option must be null and abnormal_reason must be "unclear_mark".
- Use unclear_mark only when there is an actual hand-drawn mark inside an option cell and its type or target option cannot be determined.
- If the ballot has only one checkable option such as "agree if checked", an unchecked row is a valid negative/not-agreed result, not an abnormal row.
- If the ballot has two or more options and a row has no checked option, selected_option must be null and abnormal_reason must be "missing_mark".
- If a row has multiple checked options, selected_option must be null and abnormal_reason must be "multiple_marks".
- abnormal_reason must be one of: "invalid_mark", "missing_mark", "multiple_marks", "unclear_mark", "unreadable_candidate_name", or "" for normal rows.
- Do not calculate final totals. Only output row-level recognition.
- Output must be JSON only, with no markdown or explanation.

Required JSON shape:
{
  "title": "string",
  "options": [{"code": "string", "label": "string"}],
  "candidates": [
    {
      "row_index": 1,
      "name": "string",
      "marks": [
        {
          "option": "string",
          "option_label": "string",
          "mark_type": "check_mark|tick|circle|x_mark|cross|filled_block|black_dot|scribble|line|heavy_shading|unclear",
          "is_valid_check": true
        }
      ],
      "checked_options": ["string"],
      "selected_option": "string or null",
      "selected_option_label": "string or null",
      "abnormal": false,
      "abnormal_reason": ""
    }
  ]
}
"""

VISION_USER_PROMPT = "Recognize this voting ballot and output only the required JSON object."

VALID_MARK_TYPES = {"check", "check_mark", "tick", "v", "v_mark", "hook"}
INVALID_MARK_TYPES = {
    "circle",
    "o",
    "x",
    "x_mark",
    "cross",
    "filled_block",
    "black_block",
    "black_square",
    "black_dot",
    "dot",
    "scribble",
    "line",
    "heavy_shading",
    "shading",
}


@dataclass(slots=True)
class VisionModelConfig:
    api_url: str
    api_key: str
    model: str
    timeout_seconds: int = 90
    provider: str = "openai_compatible"
    use_response_format: bool = True
    enable_thinking: bool = False

    @classmethod
    def from_env(cls) -> "VisionModelConfig":
        provider = os.getenv("VISION_MODEL_PROVIDER", "openai_compatible").strip().lower()
        default_api_url = (
            "https://open.bigmodel.cn/api/paas/v4/chat/completions"
            if provider == "zhipu"
            else "https://api.openai.com/v1/chat/completions"
        )
        api_url = os.getenv("VISION_MODEL_API_URL", default_api_url)
        api_key = os.getenv("VISION_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY", "")
        model = os.getenv("VISION_MODEL_NAME", "")
        timeout_seconds = int(os.getenv("VISION_MODEL_TIMEOUT_SECONDS", "90"))

        response_format_env = os.getenv("VISION_MODEL_RESPONSE_FORMAT", "").strip().lower()
        if response_format_env:
            use_response_format = response_format_env not in {"0", "false", "none", "off", "disabled"}
        else:
            use_response_format = provider != "zhipu"

        thinking_env = os.getenv("VISION_MODEL_THINKING", "").strip().lower()
        enable_thinking = provider == "zhipu" if not thinking_env else thinking_env in {"1", "true", "on", "enabled"}

        if not api_key:
            raise RuntimeError("Missing VISION_MODEL_API_KEY or OPENAI_API_KEY.")
        if not model:
            raise RuntimeError("Missing VISION_MODEL_NAME. Set it to your vision-capable model name.")
        try:
            api_key.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise RuntimeError(
                "VISION_MODEL_API_KEY contains non-ASCII characters. "
                "Please paste the real API key only, without Chinese placeholder text or comments."
            ) from exc

        return cls(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            provider=provider,
            use_response_format=use_response_format,
            enable_thinking=enable_thinking,
        )


class OpenAICompatibleVisionClient:
    def __init__(self, config: VisionModelConfig | None = None) -> None:
        self.config = config or VisionModelConfig.from_env()

    def recognize_image(self, image_path: str | Path) -> dict[str, Any]:
        if self.config.provider == "zhipu":
            return self._recognize_image_with_zhipu_sdk(image_path)

        image_url = _image_to_raw_base64(image_path) if self.config.provider == "zhipu" else _image_to_data_url(image_path)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_USER_PROMPT},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
        }
        if self.config.use_response_format:
            payload["response_format"] = {"type": "json_object"}
        if self.config.enable_thinking:
            payload["thinking"] = {"type": "enabled"}

        request = urllib.request.Request(
            self.config.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Vision model request failed: HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise RuntimeError(
                "Vision model request failed before receiving a valid response. "
                f"Provider={self.config.provider}, model={self.config.model}, url={self.config.api_url}. "
                f"Original error: {exc}"
            ) from exc

        content = response_payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return _parse_json_content(str(content))

    def _recognize_image_with_zhipu_sdk(self, image_path: str | Path) -> dict[str, Any]:
        try:
            from zai import ZhipuAiClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Zhipu provider requires zai-sdk. Install it with: pip install zai-sdk"
            ) from exc

        client = ZhipuAiClient(api_key=self.config.api_key)
        response = client.chat.completions.create(
            model=self.config.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": _image_to_raw_base64(image_path)}},
                        {
                            "type": "text",
                            "text": f"{VISION_SYSTEM_PROMPT}\n\n{VISION_USER_PROMPT}",
                        },
                    ],
                }
            ],
            thinking={"type": "enabled"} if self.config.enable_thinking else {"type": "disabled"},
        )

        message = response.choices[0].message
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return _parse_json_content(str(content or ""))


class VisionBallotRecognizer:
    def __init__(self, client: OpenAICompatibleVisionClient | None = None) -> None:
        self.client = client

    def recognize(self, image_path: str | Path) -> BallotRecognitionResult:
        if self.client is None:
            self.client = OpenAICompatibleVisionClient()
        raw = self.client.recognize_image(image_path)
        return normalize_vision_result(raw, image_path)

    def recognize_with_fallback(self, image_path: str | Path) -> BallotRecognitionResult:
        try:
            return self.recognize(image_path)
        except Exception as exc:
            return build_vision_failure_result(image_path, str(exc))


def normalize_vision_result(raw: dict[str, Any], image_path: str | Path) -> BallotRecognitionResult:
    raw = _validate_vision_payload(raw)
    options = raw.get("options") or []
    option_labels: dict[str, str] = {}
    label_to_code: dict[str, str] = {}
    for index, option in enumerate(options, start=1):
        label = str(option.get("label") or option.get("code") or f"option_{index}").strip()
        code = str(option.get("code") or "").strip() or _make_option_code(label, index)
        code = _dedupe_code(code, option_labels)
        option_labels[code] = label
        label_to_code[label] = code
        label_to_code[_normalize_text_key(label)] = code
        label_to_code[_normalize_text_key(code)] = code

    single_option_negative_code: str | None = None
    if len(option_labels) == 1:
        positive_label = next(iter(option_labels.values()))
        single_option_negative_code = _dedupe_code("not_checked", option_labels)
        negative_label = _single_option_negative_label(positive_label)
        option_labels[single_option_negative_code] = negative_label
        label_to_code[negative_label] = single_option_negative_code
        label_to_code[_normalize_text_key(negative_label)] = single_option_negative_code
        label_to_code[_normalize_text_key(single_option_negative_code)] = single_option_negative_code

    rows: list[RowRecognition] = []
    summary = {code: 0 for code in option_labels}
    abnormal_types: set[str] = set()
    missing_mark_rows: list[int] = []
    ambiguous_rows: list[int] = []

    candidates = raw.get("candidates") or []
    for fallback_index, item in enumerate(candidates, start=1):
        row_index = _safe_int(item.get("row_index"), fallback_index)
        selected = item.get("selected_option")
        selected_label = item.get("selected_option_label")
        checked_options = _normalize_checked_options(item.get("checked_options"), option_labels, label_to_code)
        marks = _normalize_marks(item.get("marks"), option_labels, label_to_code)
        has_invalid_mark = any(_mark_is_invalid(mark) for mark in marks)
        valid_mark_options = _valid_mark_options(marks)
        if valid_mark_options:
            checked_options = valid_mark_options
        selected_code = _normalize_selected_option(selected, selected_label, option_labels, label_to_code)
        abnormal_reason = str(item.get("abnormal_reason") or "").strip()
        is_abnormal = bool(item.get("abnormal")) or bool(abnormal_reason)
        is_invalid_vote_mark = abnormal_reason in {"invalid_mark", "unclear_mark"}

        if has_invalid_mark:
            selected_code = None
            checked_options = []
            abnormal_reason = "invalid_mark"
            is_abnormal = True
        elif is_invalid_vote_mark:
            selected_code = None
            is_abnormal = True
        elif checked_options is not None:
            if len(checked_options) == 0:
                if single_option_negative_code is not None:
                    selected_code = single_option_negative_code
                    abnormal_reason = ""
                    is_abnormal = False
                else:
                    selected_code = None
                    abnormal_reason = "missing_mark"
                    is_abnormal = True
            elif len(checked_options) == 1:
                selected_code = checked_options[0]
                if abnormal_reason == "missing_mark":
                    abnormal_reason = ""
                is_abnormal = bool(abnormal_reason)
            else:
                selected_code = None
                abnormal_reason = "multiple_marks"
                is_abnormal = True

        if selected_code is not None and selected_code not in option_labels:
            selected_code = None
            abnormal_reason = "unknown_option"
            is_abnormal = True

        if selected_code is None:
            if single_option_negative_code is not None and abnormal_reason in {"", "missing_mark"}:
                selected_code = single_option_negative_code
                abnormal_reason = ""
                is_abnormal = False
            else:
                if not abnormal_reason:
                    abnormal_reason = "missing_mark"
                is_abnormal = True

        if selected_code is not None:
            summary.setdefault(selected_code, 0)
            summary[selected_code] += 1

        if abnormal_reason == "missing_mark":
            missing_mark_rows.append(row_index)
            abnormal_types.add("missing_marks")
        if abnormal_reason == "multiple_marks":
            ambiguous_rows.append(row_index)
            abnormal_types.add("multiple_marks")
        if is_abnormal and abnormal_reason not in {"", "missing_mark", "multiple_marks"}:
            abnormal_types.add(abnormal_reason)

        rows.append(
            RowRecognition(
                row_index=row_index,
                candidate_name=str(item.get("name") or f"row-{row_index}").strip(),
                recognized_name=str(item.get("name") or "").strip(),
                selected_option=selected_code,
                option_scores={},
                is_ambiguous=is_abnormal,
                issue=abnormal_reason or None,
                row_bbox=item.get("bbox") or [],
            )
        )

    valid_vote_count = sum(1 for row in rows if row.selected_option is not None)
    if rows and valid_vote_count == 0:
        abnormal_types.add("blank_ballot")

    return BallotRecognitionResult(
        template_name="vision_model",
        image_path=str(image_path),
        summary=summary,
        rows=rows,
        is_abnormal=bool(abnormal_types),
        abnormal_types=sorted(abnormal_types),
        abnormal_details={
            "valid_vote_count": valid_vote_count,
            "ambiguous_rows": ambiguous_rows,
            "missing_mark_rows": missing_mark_rows,
        },
        recognition_source="vision_model",
        title=str(raw.get("title") or ""),
        option_labels=option_labels,
        raw_model_output=raw,
    )


def _image_to_data_url(image_path: str | Path) -> str:
    path = Path(image_path)
    encoded = _image_to_raw_base64(path)
    return f"data:{_guess_image_mime_type(path)};base64,{encoded}"


def _image_to_raw_base64(image_path: str | Path) -> str:
    return base64.b64encode(Path(image_path).read_bytes()).decode("ascii")


def build_vision_failure_result(image_path: str | Path, error_message: str) -> BallotRecognitionResult:
    return BallotRecognitionResult(
        template_name="vision_model",
        image_path=str(image_path),
        summary={},
        rows=[],
        is_abnormal=True,
        abnormal_types=["recognition_failed"],
        abnormal_details={
            "valid_vote_count": 0,
            "ambiguous_rows": [],
            "missing_mark_rows": [],
            "error_message": error_message,
        },
        recognition_source="vision_model",
        title="",
        option_labels={},
        raw_model_output={"error": error_message},
    )


def _parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_first_json_object(text)
        if extracted is None:
            raise
        return json.loads(extracted)


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _validate_vision_payload(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Vision model output must be a JSON object.")
    options = raw.get("options")
    candidates = raw.get("candidates")
    if not isinstance(options, list) or not options:
        raise ValueError("Vision model output is missing a non-empty options list.")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Vision model output is missing a non-empty candidates list.")

    for index, option in enumerate(options, start=1):
        if not isinstance(option, dict):
            raise ValueError(f"Option #{index} must be an object.")
        label = str(option.get("label") or option.get("code") or "").strip()
        if not label:
            raise ValueError(f"Option #{index} is missing label/code.")

    seen_rows: set[int] = set()
    for fallback_index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            raise ValueError(f"Candidate #{fallback_index} must be an object.")
        row_index = _safe_int(candidate.get("row_index"), fallback_index)
        if row_index in seen_rows:
            raise ValueError(f"Duplicate candidate row_index: {row_index}.")
        seen_rows.add(row_index)
        if not str(candidate.get("name") or "").strip():
            raise ValueError(f"Candidate row {row_index} is missing name.")
        if "checked_options" not in candidate:
            raise ValueError(f"Candidate row {row_index} is missing checked_options.")
        if not isinstance(candidate.get("checked_options"), list):
            raise ValueError(f"Candidate row {row_index} checked_options must be a list.")
        marks = candidate.get("marks")
        if marks is not None:
            if not isinstance(marks, list):
                raise ValueError(f"Candidate row {row_index} marks must be a list.")
            for mark_index, mark in enumerate(marks, start=1):
                if not isinstance(mark, dict):
                    raise ValueError(f"Candidate row {row_index} mark #{mark_index} must be an object.")
    return raw


def _make_option_code(label: str, index: int) -> str:
    ascii_code = re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_").lower()
    return ascii_code or f"option_{index}"


def _dedupe_code(code: str, existing: dict[str, str]) -> str:
    if code not in existing:
        return code
    index = 2
    while f"{code}_{index}" in existing:
        index += 1
    return f"{code}_{index}"


def _single_option_negative_label(positive_label: str) -> str:
    if "同意" in positive_label and "不同意" not in positive_label:
        return "不同意"
    if "赞成" in positive_label and "不赞成" not in positive_label:
        return "不赞成"
    if "通过" in positive_label and "未通过" not in positive_label:
        return "未通过"
    if "合格" in positive_label and "不合格" not in positive_label:
        return "不合格"
    return "未勾选"


def _normalize_selected_option(
    selected: Any,
    selected_label: Any,
    option_labels: dict[str, str],
    label_to_code: dict[str, str],
) -> str | None:
    if selected is None and selected_label is None:
        return None
    selected_text = str(selected or "").strip()
    selected_label_text = str(selected_label or "").strip()
    if _is_empty_selection(selected_text) and _is_empty_selection(selected_label_text):
        return None
    if selected_text in option_labels:
        return selected_text
    if selected_text in label_to_code:
        return label_to_code[selected_text]
    if _normalize_text_key(selected_text) in label_to_code:
        return label_to_code[_normalize_text_key(selected_text)]
    if selected_label_text in label_to_code:
        return label_to_code[selected_label_text]
    if _normalize_text_key(selected_label_text) in label_to_code:
        return label_to_code[_normalize_text_key(selected_label_text)]
    return selected_text or None


def _normalize_checked_options(
    checked_options: Any,
    option_labels: dict[str, str],
    label_to_code: dict[str, str],
) -> list[str] | None:
    if checked_options is None:
        return None
    if not isinstance(checked_options, list):
        checked_options = [checked_options]
    normalized: list[str] = []
    for item in checked_options:
        value = str(item or "").strip()
        if not value:
            continue
        if value in option_labels:
            code = value
        elif value in label_to_code:
            code = label_to_code[value]
        elif _normalize_text_key(value) in label_to_code:
            code = label_to_code[_normalize_text_key(value)]
        else:
            code = value
        if code not in normalized:
            normalized.append(code)
    return normalized


def _normalize_marks(
    marks: Any,
    option_labels: dict[str, str],
    label_to_code: dict[str, str],
) -> list[dict[str, Any]]:
    if not isinstance(marks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        option = mark.get("option") or mark.get("option_code") or mark.get("selected_option")
        option_label = mark.get("option_label") or mark.get("label") or mark.get("selected_option_label")
        normalized.append(
            {
                "option_code": _normalize_selected_option(option, option_label, option_labels, label_to_code),
                "mark_type": _normalize_mark_type(mark.get("mark_type") or mark.get("type") or mark.get("shape")),
                "is_valid_check": _normalize_optional_bool(mark.get("is_valid_check")),
            }
        )
    return normalized


def _normalize_mark_type(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    aliases = {
        "checkmark": "check_mark",
        "check_mark": "check_mark",
        "check": "check",
        "tick_mark": "tick",
        "tick": "tick",
        "v_shape": "v",
        "v_mark": "v_mark",
        "hook": "hook",
        "o_mark": "circle",
        "round": "circle",
        "circle_mark": "circle",
        "xmark": "x_mark",
        "x_mark": "x_mark",
        "cross_mark": "cross",
        "black_square": "filled_block",
        "black_block": "filled_block",
        "filled_square": "filled_block",
        "solid_block": "filled_block",
        "black_dot": "black_dot",
        "heavy_shade": "heavy_shading",
    }
    return aliases.get(text, text)


def _normalize_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return None


def _mark_is_invalid(mark: dict[str, Any]) -> bool:
    mark_type = str(mark.get("mark_type") or "")
    if mark_type in INVALID_MARK_TYPES:
        return True
    return False


def _valid_mark_options(marks: list[dict[str, Any]]) -> list[str]:
    options: list[str] = []
    for mark in marks:
        option_code = mark.get("option_code")
        if not isinstance(option_code, str) or not option_code:
            continue
        mark_type = str(mark.get("mark_type") or "")
        is_valid_check = mark.get("is_valid_check")
        if mark_type in VALID_MARK_TYPES or is_valid_check is True:
            if option_code not in options:
                options.append(option_code)
    return options


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_text_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _is_empty_selection(value: str) -> bool:
    return _normalize_text_key(value) in {"", "none", "null", "无", "未选择", "未识别"}


def _clamp_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _guess_image_mime_type(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".bmp":
        return "image/bmp"
    if suffix in {".tif", ".tiff"}:
        return "image/tiff"
    return "application/octet-stream"
