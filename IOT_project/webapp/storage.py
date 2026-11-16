from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import socket
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from fastapi import HTTPException, Request, UploadFile

from ballot_recognition.vision_batch import SUPPORTED_IMAGE_SUFFIXES, VisionBatchBallotRecognizer

try:
    import cv2
except Exception:  # pragma: no cover - optional runtime acceleration
    cv2 = None


ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "webapp_frontend"
DATA_DIR = Path(os.getenv("APP_DATA_DIR", str(ROOT_DIR / "webapp_data"))).resolve()
STATE_PATH = DATA_DIR / "state.json"
ACTIVITIES_DIR = DATA_DIR / "activities"
LEGACY_UPLOADS_DIR = DATA_DIR / "uploads"
LEGACY_RESULTS_DIR = DATA_DIR / "results"
DEFAULT_ACTIVITY_NAME = "未命名投票活动"
DEFAULT_INVALID_MARK_RULES = ["circle", "x_mark", "filled_block", "black_dot", "scribble", "heavy_shading", "line"]
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
PERCEPTUAL_HASH_DISTANCE_THRESHOLD = 10

STATE_LOCK = threading.Lock()
recognizer = VisionBatchBallotRecognizer()

def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVITIES_DIR.mkdir(parents=True, exist_ok=True)
    LEGACY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    LEGACY_RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _iso_now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_activity_name(value: str | None) -> str:
    name = str(value or "").strip()
    return name or DEFAULT_ACTIVITY_NAME


def _clean_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\n,，、;；/|]+", value)
    elif isinstance(value, list):
        parts = value
    else:
        parts = []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in parts:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_activity_template(raw: dict[str, Any]) -> dict[str, Any]:
    template = raw.get("template") if isinstance(raw.get("template"), dict) else {}
    candidates = _clean_string_list(template.get("candidates") or raw.get("candidates"))
    options = _clean_string_list(template.get("options") or raw.get("options"))
    invalid_mark_rules = _clean_string_list(template.get("invalid_mark_rules") or raw.get("invalid_mark_rules"))
    if not invalid_mark_rules:
        invalid_mark_rules = DEFAULT_INVALID_MARK_RULES.copy()

    return {
        "candidates": candidates,
        "options": options,
        "invalid_mark_rules": invalid_mark_rules,
    }


def _template_signature(activity_name: str, template: dict[str, Any]) -> str:
    payload = {
        "activity_name": _clean_activity_name(activity_name),
        "candidates": _clean_string_list(template.get("candidates")),
        "options": _clean_string_list(template.get("options")),
        "invalid_mark_rules": _clean_string_list(template.get("invalid_mark_rules")),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _normalize_activity_template_entry(raw: dict[str, Any]) -> dict[str, Any]:
    activity_name = _clean_activity_name(raw.get("activity_name") or raw.get("template_name"))
    template = _normalize_activity_template(raw)
    template_id = str(raw.get("template_id") or "").strip() or f"tpl_{_template_signature(activity_name, template)[:16]}"
    created_at = str(raw.get("created_at") or _iso_now())
    updated_at = str(raw.get("updated_at") or created_at)
    return {
        "template_id": template_id,
        "template_name": str(raw.get("template_name") or activity_name).strip() or activity_name,
        "activity_name": activity_name,
        "created_at": created_at,
        "updated_at": updated_at,
        "signature": _template_signature(activity_name, template),
        "template": template,
    }


def _activity_template_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return _normalize_activity_template_entry(
        {
            "activity_name": meta.get("activity_name"),
            "template_name": meta.get("activity_name"),
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "template": meta.get("template") or {},
        }
    )


def _dedupe_activity_templates(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_signature: dict[str, dict[str, Any]] = {}
    for item in templates:
        normalized = _normalize_activity_template_entry(item)
        signature = normalized["signature"]
        existing = by_signature.get(signature)
        if existing is None or str(normalized.get("updated_at") or "") >= str(existing.get("updated_at") or ""):
            by_signature[signature] = normalized
    result = list(by_signature.values())
    result.sort(key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""), reverse=True)
    return result[:50]


def _remember_activity_template(state: dict[str, Any], meta: dict[str, Any]) -> None:
    templates = list(state.get("activity_templates") or [])
    templates.append(_activity_template_from_meta(meta))
    state["activity_templates"] = _dedupe_activity_templates(templates)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_remove_path(path: Path) -> None:
    if not path.exists():
        return
    if not _is_within(path, DATA_DIR):
        raise RuntimeError(f"Refusing to remove path outside data directory: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def _normalize_activity_meta(raw: dict[str, Any]) -> dict[str, Any]:
    activity_id = str(raw.get("activity_id") or raw.get("batch_id") or "").strip()
    if not activity_id:
        activity_id = _new_id()

    created_at = str(raw.get("created_at") or _iso_now())
    updated_at = str(raw.get("updated_at") or created_at)
    legacy_batch_ids = raw.get("legacy_batch_ids")
    if legacy_batch_ids is None:
        legacy_batch_id = raw.get("legacy_batch_id") or raw.get("batch_id")
        legacy_batch_ids = [legacy_batch_id] if legacy_batch_id else []

    meta = {
        "activity_id": activity_id,
        "activity_name": _clean_activity_name(raw.get("activity_name")),
        "created_at": created_at,
        "updated_at": updated_at,
        "upload_count": int(raw.get("upload_count") or 0),
        "ballot_count": int(raw.get("ballot_count") or 0),
        "abnormal_ballot_count": int(raw.get("abnormal_ballot_count") or 0),
        "recognition_mode": str(raw.get("recognition_mode") or "vision_model"),
        "legacy_batch_ids": [str(item) for item in legacy_batch_ids if str(item).strip()],
        "template": _normalize_activity_template(raw),
    }
    return meta


def _load_state() -> dict[str, Any]:
    _ensure_storage()
    raw = _read_json(STATE_PATH, {})

    if isinstance(raw, dict) and "activities" in raw:
        activities = [_normalize_activity_meta(item) for item in raw.get("activities", []) if isinstance(item, dict)]
        raw_templates = raw.get("activity_templates")
        if isinstance(raw_templates, list):
            activity_templates = _dedupe_activity_templates([item for item in raw_templates if isinstance(item, dict)])
        else:
            activity_templates = _dedupe_activity_templates([_activity_template_from_meta(item) for item in activities])
        state = {
            "latest_activity_id": raw.get("latest_activity_id"),
            "activities": activities,
            "activity_templates": activity_templates,
        }
        _sort_activities(state["activities"])
        return state

    batches = raw.get("batches", []) if isinstance(raw, dict) else []
    activities = []
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        activities.append(
            _normalize_activity_meta(
                {
                    "activity_id": batch.get("batch_id"),
                    "activity_name": batch.get("activity_name"),
                    "created_at": batch.get("created_at"),
                    "updated_at": batch.get("created_at"),
                    "upload_count": 1 if batch.get("batch_id") else 0,
                    "ballot_count": batch.get("ballot_count", 0),
                    "abnormal_ballot_count": batch.get("abnormal_ballot_count", 0),
                    "recognition_mode": batch.get("recognition_mode", "vision_model"),
                    "legacy_batch_ids": [batch.get("batch_id")] if batch.get("batch_id") else [],
                }
            )
        )

    state = {
        "latest_activity_id": raw.get("latest_batch_id") if isinstance(raw, dict) else None,
        "activities": activities,
        "activity_templates": _dedupe_activity_templates([_activity_template_from_meta(item) for item in activities]),
    }
    _sort_activities(state["activities"])
    _save_state(state)
    return state


def _save_state(state: dict[str, Any]) -> None:
    payload = {
        "latest_activity_id": state.get("latest_activity_id"),
        "activities": [_normalize_activity_meta(item) for item in state.get("activities", [])],
        "activity_templates": _dedupe_activity_templates([item for item in state.get("activity_templates", []) if isinstance(item, dict)]),
    }
    _sort_activities(payload["activities"])
    _write_json(STATE_PATH, payload)


def _sort_activities(activities: list[dict[str, Any]]) -> None:
    activities.sort(key=lambda item: (item.get("updated_at") or "", item.get("created_at") or "", item.get("activity_id") or ""), reverse=True)


def _find_activity_meta(state: dict[str, Any], activity_id: str) -> dict[str, Any] | None:
    for item in state.get("activities", []):
        if item.get("activity_id") == activity_id:
            return item
    return None


def _require_activity_meta(state: dict[str, Any], activity_id: str) -> dict[str, Any]:
    meta = _find_activity_meta(state, activity_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="活动不存在。")
    return meta


def _activity_dir(activity_id: str) -> Path:
    return ACTIVITIES_DIR / activity_id


def _activity_uploads_dir(activity_id: str) -> Path:
    return _activity_dir(activity_id) / "uploads"


def _upload_dir(activity_id: str, upload_id: str) -> Path:
    return _activity_uploads_dir(activity_id) / upload_id


def _upload_result_path(activity_id: str, upload_id: str) -> Path:
    return _upload_dir(activity_id, upload_id) / "result.json"


def _legacy_result_path(batch_id: str) -> Path:
    return LEGACY_RESULTS_DIR / batch_id / "result.json"


def _path_to_data_url(path: str | Path | None) -> str | None:
    if not path:
        return None
    real_path = Path(path)
    if not real_path.exists() or not _is_within(real_path, DATA_DIR):
        return None
    relative = real_path.resolve().relative_to(DATA_DIR.resolve())
    return f"/data/{relative.as_posix()}"


def _file_sha256(path: str | Path | None) -> str | None:
    if not path:
        return None
    real_path = Path(path)
    if not real_path.exists() or not real_path.is_file():
        return None
    digest = hashlib.sha256()
    with real_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_dhash(path: str | Path | None) -> str | None:
    if cv2 is None or not path:
        return None
    real_path = Path(path)
    if not real_path.exists() or not real_path.is_file():
        return None
    image = cv2.imread(str(real_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    image = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
    bits = image[:, 1:] > image[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"


def _hamming_distance_hex(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def _ballot_content_fingerprint(ballot: dict[str, Any]) -> str:
    row_parts = []
    raw_candidates = {
        _safe_int(item.get("row_index"), index): item
        for index, item in enumerate((ballot.get("raw_model_output") or {}).get("candidates") or [], start=1)
        if isinstance(item, dict)
    }
    for index, row in enumerate(ballot.get("rows") or [], start=1):
        row_index = _safe_int(row.get("row_index"), index)
        raw_row = raw_candidates.get(row_index) or {}
        mark_parts = []
        for mark in raw_row.get("marks") or []:
            if not isinstance(mark, dict):
                continue
            mark_parts.append(
                ":".join(
                    [
                        str(mark.get("option") or mark.get("option_label") or ""),
                        str(mark.get("mark_type") or mark.get("type") or ""),
                        str(mark.get("is_valid_check") or ""),
                    ]
                )
            )
        row_parts.append(
            "|".join(
                [
                    str(row_index),
                    str(row.get("candidate_name") or row.get("recognized_name") or ""),
                    str(row.get("selected_option") or ""),
                    str(row.get("issue") or ""),
                    ",".join(sorted(mark_parts)),
                ]
            )
        )
    source = "\n".join(row_parts)
    return hashlib.sha256(source.encode("utf-8")).hexdigest() if source else ""


def _empty_duplicate_fields() -> dict[str, Any]:
    return {
        "is_duplicate": False,
        "duplicate_group_id": None,
        "duplicate_group_kind": None,
        "duplicate_confidence": None,
        "duplicate_reason": "",
        "duplicate_count": 1,
    }


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        for ip in addresses:
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    return "127.0.0.1"


def _external_base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    host_header = forwarded_host or request.headers.get("host") or ""
    scheme = forwarded_proto or request.url.scheme

    if host_header:
        hostname = host_header
        if ":" in host_header and not host_header.startswith("["):
            hostname, port_text = host_header.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                port = 443 if scheme == "https" else 80
        else:
            port = 443 if scheme == "https" else 80
    else:
        hostname = request.url.hostname or "127.0.0.1"
        port = request.url.port or (443 if scheme == "https" else 80)

    if hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
        hostname = _detect_lan_ip()

    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if default_port:
        return f"{scheme}://{hostname}"
    return f"{scheme}://{hostname}:{port}"


def _absolute_or_relative_mobile_url(request: Request | None, activity_id: str) -> str:
    relative = f"/m/activities/{activity_id}"
    if request is None:
        return relative
    return _external_base_url(request) + relative


def _sanitize_filename(original_name: str, index: int, existing: set[str]) -> str:
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {original_name}")
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(original_name).stem).strip("_")
    if not stem:
        stem = f"image_{index:03d}"
    candidate = f"{index:03d}_{stem}{suffix}"
    serial = 2
    while candidate in existing:
        candidate = f"{index:03d}_{stem}_{serial}{suffix}"
        serial += 1
    existing.add(candidate)
    return candidate


def _normalize_ballot_payload(ballot: dict[str, Any], upload_id: str, fallback_index: int) -> dict[str, Any]:
    image_path = ballot.get("image_path")
    file_name = ballot.get("file_name") or (Path(image_path).name if image_path else f"ballot_{fallback_index:03d}.png")
    option_labels = ballot.get("option_labels") or {}
    raw_summary = ballot.get("summary") or {}
    summary: dict[str, int] = {}
    for key, value in raw_summary.items():
        label = option_labels.get(key, key)
        summary[str(label)] = int(value or 0)

    review = ballot.get("review") or {}
    original_url = ballot.get("original_url")
    if not original_url:
        original_url = _path_to_data_url(image_path)
    file_hash = ballot.get("file_hash") or _file_sha256(image_path)
    perceptual_hash = ballot.get("perceptual_hash") or _image_dhash(image_path)
    content_fingerprint = ballot.get("content_fingerprint") or _ballot_content_fingerprint(ballot)

    payload = {
        "ballot_id": ballot.get("ballot_id") or f"{upload_id}_{fallback_index:03d}",
        "upload_id": upload_id,
        "file_name": file_name,
        "image_path": image_path,
        "original_url": original_url or "",
        "summary": summary,
        "rows": ballot.get("rows") or [],
        "is_abnormal": bool(ballot.get("is_abnormal")),
        "abnormal_types": ballot.get("abnormal_types") or [],
        "abnormal_details": ballot.get("abnormal_details") or {},
        "recognition_source": ballot.get("recognition_source") or "vision_model",
        "title": ballot.get("title") or "",
        "option_labels": option_labels,
        "raw_model_output": ballot.get("raw_model_output") or {},
        "file_hash": file_hash,
        "perceptual_hash": perceptual_hash,
        "content_fingerprint": content_fingerprint,
        **_empty_duplicate_fields(),
        "review": {
            "reviewed": bool(review.get("reviewed")),
            "comment": str(review.get("comment") or ""),
            "updated_at": review.get("updated_at"),
        },
    }
    return payload


def _normalize_upload_payload(raw: dict[str, Any], source_path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    upload_id = str(raw.get("upload_id") or raw.get("batch_id") or source_path.parent.name)
    created_at = str(raw.get("created_at") or meta.get("created_at") or _iso_now())
    ballots = [_normalize_ballot_payload(ballot, upload_id, index) for index, ballot in enumerate(raw.get("ballots", []), start=1)]
    for ballot in ballots:
        ballot["created_at"] = created_at
    abnormal_type_totals = {str(key): int(value or 0) for key, value in (raw.get("abnormal_type_totals") or {}).items()}

    payload = {
        "upload_id": upload_id,
        "activity_id": meta["activity_id"],
        "activity_name": meta["activity_name"],
        "created_at": created_at,
        "recognition_mode": raw.get("recognition_mode") or meta.get("recognition_mode") or "vision_model",
        "template_name": raw.get("template_name") or "vision_model",
        "ballot_count": int(raw.get("ballot_count") or len(ballots)),
        "option_totals": {str(key): int(value or 0) for key, value in (raw.get("option_totals") or {}).items()},
        "candidate_totals": {
            str(candidate): {str(option): int(count or 0) for option, count in (counts or {}).items()}
            for candidate, counts in (raw.get("candidate_totals") or {}).items()
        },
        "ballots": ballots,
        "abnormal_ballot_count": int(raw.get("abnormal_ballot_count") or sum(1 for ballot in ballots if ballot.get("is_abnormal"))),
        "abnormal_type_totals": abnormal_type_totals,
    }
    return _recalculate_upload_payload(payload, meta)


def _iter_activity_result_paths(meta: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for batch_id in meta.get("legacy_batch_ids", []):
        path = _legacy_result_path(batch_id)
        if path.exists():
            paths.append(path)

    upload_root = _activity_uploads_dir(meta["activity_id"])
    if upload_root.exists():
        new_paths = sorted(upload_root.glob("*/result.json"), reverse=True)
        paths.extend(new_paths)
    return paths


def _load_activity_uploads(meta: dict[str, Any]) -> list[dict[str, Any]]:
    uploads: list[dict[str, Any]] = []
    for path in _iter_activity_result_paths(meta):
        raw = _read_json(path, {})
        if not isinstance(raw, dict):
            continue
        uploads.append(_normalize_upload_payload(raw, path, meta))
    uploads.sort(key=lambda item: (item.get("created_at") or "", item.get("upload_id") or ""), reverse=True)
    return uploads


def _merge_nested_counts(target: dict[str, dict[str, int]], incoming: dict[str, dict[str, int]]) -> None:
    for name, counts in incoming.items():
        bucket = target.setdefault(name, {})
        for key, value in counts.items():
            bucket[key] = bucket.get(key, 0) + int(value or 0)


def _merge_flat_counts(target: dict[str, int], incoming: dict[str, int]) -> None:
    for key, value in incoming.items():
        target[key] = target.get(key, 0) + int(value or 0)


def _issue_to_abnormal_type(issue: str | None) -> str | None:
    if not issue:
        return None
    if issue == "missing_mark":
        return "missing_marks"
    return issue


def _option_label(ballot: dict[str, Any], option: Any) -> str | None:
    if option is None:
        return None
    text = str(option).strip()
    if not text:
        return None
    option_labels = ballot.get("option_labels") or {}
    return str(option_labels.get(text) or text)


def _option_key(text: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or "").casefold())


def _is_positive_single_option_label(text: Any) -> bool:
    key = _option_key(text)
    return key in {
        "agree",
        "agreed",
        "checked",
        "selected",
        "yes",
        "true",
        "pass",
        "passed",
        "approve",
        "approved",
        "同意",
        "赞成",
        "通过",
        "已勾选",
        "勾选",
        "选中",
    }


def _is_negative_single_option_label(text: Any) -> bool:
    key = _option_key(text)
    return key in {
        "notchecked",
        "unchecked",
        "unselected",
        "disagree",
        "disagreed",
        "no",
        "false",
        "fail",
        "failed",
        "reject",
        "rejected",
        "不同意",
        "反对",
        "不通过",
        "未通过",
        "未勾选",
        "未选中",
        "不勾选",
    }


def _canonical_option_label(ballot: dict[str, Any], option: Any, option_names: list[str]) -> str | None:
    label = _option_label(ballot, option)
    if label is None:
        return None
    if label in option_names:
        return label

    label_key = _option_key(label)
    for option_name in option_names:
        option_key = _option_key(option_name)
        if label_key and option_key and label_key == option_key:
            return option_name

    if len(option_names) == 1:
        raw_key = _option_key(option)
        option_key = _option_key(option_names[0])
        if _is_negative_single_option_label(label) or _is_negative_single_option_label(option):
            return None
        if _is_positive_single_option_label(label) or _is_positive_single_option_label(option):
            return option_names[0]
        if label_key and option_key and (label_key in option_key or option_key in label_key):
            return option_names[0]
        if raw_key and option_key and (raw_key in option_key or option_key in raw_key):
            return option_names[0]

    return label


def _option_names_for_upload(meta: dict[str, Any], ballots: list[dict[str, Any]]) -> list[str]:
    names = _clean_string_list((meta.get("template") or {}).get("options"))
    if names:
        return names
    seen = set(names)
    for ballot in ballots:
        for label in (ballot.get("option_labels") or {}).values():
            text = str(label or "").strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
        for label in (ballot.get("summary") or {}).keys():
            text = str(label or "").strip()
            if text and text not in seen:
                seen.add(text)
                names.append(text)
    return names


def _recalculate_ballot_payload(ballot: dict[str, Any], option_names: list[str]) -> dict[str, Any]:
    rows = ballot.get("rows") or []
    summary = {option: 0 for option in option_names}
    abnormal_types: set[str] = set()
    if not rows:
        abnormal_types.update(str(item) for item in ballot.get("abnormal_types", []) if str(item).strip())

    valid_vote_count = 0
    for row in rows:
        issue = str(row.get("issue") or "").strip()
        selected_label = _canonical_option_label(ballot, row.get("selected_option"), option_names)
        row["is_ambiguous"] = bool(issue)
        if issue:
            abnormal_type = _issue_to_abnormal_type(issue)
            if abnormal_type:
                abnormal_types.add(abnormal_type)
            continue
        if selected_label:
            summary.setdefault(selected_label, 0)
            summary[selected_label] += 1
            valid_vote_count += 1

    if rows and valid_vote_count == 0 and not abnormal_types:
        abnormal_types.add("blank_ballot")

    details = dict(ballot.get("abnormal_details") or {})
    details["valid_vote_count"] = valid_vote_count
    ballot["summary"] = summary
    ballot["is_abnormal"] = bool(abnormal_types)
    ballot["abnormal_types"] = sorted(abnormal_types)
    ballot["abnormal_details"] = details
    return ballot


def _recalculate_upload_payload(payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    ballots = payload.get("ballots") or []
    option_names = _option_names_for_upload(meta, ballots)
    option_totals = {option: 0 for option in option_names}
    candidate_totals: dict[str, dict[str, int]] = {
        candidate: {option: 0 for option in option_names}
        for candidate in _clean_string_list((meta.get("template") or {}).get("candidates"))
    }
    abnormal_type_totals: dict[str, int] = {
        "blank_ballot": 0,
        "multiple_marks": 0,
        "missing_marks": 0,
        "recognition_failed": 0,
        "invalid_mark": 0,
        "unclear_mark": 0,
    }

    recalculated_ballots = []
    for ballot in ballots:
        recalculated = _recalculate_ballot_payload(ballot, option_names)
        recalculated_ballots.append(recalculated)
        for option, count in (recalculated.get("summary") or {}).items():
            option_totals[option] = option_totals.get(option, 0) + int(count or 0)
        for row in recalculated.get("rows") or []:
            candidate = str(row.get("candidate_name") or row.get("recognized_name") or "").strip()
            if not candidate:
                continue
            bucket = candidate_totals.setdefault(candidate, {option: 0 for option in option_names})
            for option in option_names:
                bucket.setdefault(option, 0)
            if row.get("issue"):
                continue
            selected_label = _canonical_option_label(recalculated, row.get("selected_option"), option_names)
            if selected_label:
                bucket[selected_label] = bucket.get(selected_label, 0) + 1
        for abnormal_type in recalculated.get("abnormal_types") or []:
            key = str(abnormal_type)
            abnormal_type_totals[key] = abnormal_type_totals.get(key, 0) + 1

    payload["ballots"] = recalculated_ballots
    payload["ballot_count"] = len(recalculated_ballots)
    payload["option_totals"] = option_totals
    payload["candidate_totals"] = candidate_totals
    payload["abnormal_ballot_count"] = sum(1 for ballot in recalculated_ballots if ballot.get("is_abnormal"))
    payload["abnormal_type_totals"] = abnormal_type_totals
    return payload


def _annotate_duplicate_ballots(ballots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for ballot in ballots:
        ballot.update(_empty_duplicate_fields())

    parent = list(range(len(ballots)))
    edge_kinds: dict[tuple[int, int], str] = {}
    edge_distances: dict[tuple[int, int], int] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int, kind: str, distance: int | None = None) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left
        key = tuple(sorted((left, right)))
        edge_kinds[key] = kind
        if distance is not None:
            edge_distances[key] = distance

    hash_buckets: dict[str, list[int]] = {}
    for index, ballot in enumerate(ballots):
        file_hash = ballot.get("file_hash")
        if isinstance(file_hash, str) and file_hash:
            hash_buckets.setdefault(file_hash, []).append(index)

    for indexes in hash_buckets.values():
        if len(indexes) < 2:
            continue
        for left_pos, left in enumerate(indexes):
            for right in indexes[left_pos + 1 :]:
                union(left, right, "confirmed_exact", 0)

    for left in range(len(ballots)):
        for right in range(left + 1, len(ballots)):
            if tuple(sorted((left, right))) in edge_kinds:
                continue
            left_fingerprint = ballots[left].get("content_fingerprint")
            right_fingerprint = ballots[right].get("content_fingerprint")
            if not left_fingerprint or left_fingerprint != right_fingerprint:
                continue
            distance = _hamming_distance_hex(ballots[left].get("perceptual_hash"), ballots[right].get("perceptual_hash"))
            if distance is not None and distance <= PERCEPTUAL_HASH_DISTANCE_THRESHOLD:
                union(left, right, "suspected_same_ballot", distance)

    components: dict[int, list[int]] = {}
    for index in range(len(ballots)):
        components.setdefault(find(index), []).append(index)

    duplicate_groups: list[dict[str, Any]] = []
    group_index = 1
    for indexes in components.values():
        if len(indexes) < 2:
            continue
        ordered_indexes = sorted(
            indexes,
            key=lambda index: (
                str(ballots[index].get("created_at") or ""),
                str(ballots[index].get("upload_id") or ""),
                str(ballots[index].get("file_name") or ""),
                str(ballots[index].get("ballot_id") or ""),
            ),
        )
        original_index = ordered_indexes[0]
        duplicate_indexes = ordered_indexes[1:]
        pairs = {tuple(sorted((left, right))) for pos, left in enumerate(indexes) for right in indexes[pos + 1 :]}
        kinds = {edge_kinds[pair] for pair in pairs if pair in edge_kinds}
        distances = [edge_distances[pair] for pair in pairs if pair in edge_distances]
        kind = "confirmed_exact" if kinds == {"confirmed_exact"} else "suspected_same_ballot"
        confidence = "confirmed" if kind == "confirmed_exact" else "high"
        group_id = f"dup_{group_index:03d}"
        group_index += 1
        reason = (
            "File bytes are identical."
            if kind == "confirmed_exact"
            else "Image perceptual hash is close and row-level recognition fingerprint matches."
        )
        group_ballots = [
            {
                "ballot_id": ballots[index].get("ballot_id"),
                "file_name": ballots[index].get("file_name"),
                "upload_id": ballots[index].get("upload_id"),
                "original_url": ballots[index].get("original_url"),
                "is_original": index == original_index,
                "is_duplicate": index in duplicate_indexes,
            }
            for index in ordered_indexes
        ]
        duplicate_groups.append(
            {
                "group_id": group_id,
                "kind": kind,
                "confidence": confidence,
                "reason": reason,
                "min_perceptual_distance": min(distances) if distances else 0,
                "original_ballot_id": ballots[original_index].get("ballot_id"),
                "ballots": group_ballots,
            }
        )
        for index in duplicate_indexes:
            ballots[index].update(
                {
                    "is_duplicate": True,
                    "duplicate_group_id": group_id,
                    "duplicate_group_kind": kind,
                    "duplicate_confidence": confidence,
                    "duplicate_reason": reason,
                    "duplicate_count": len(indexes),
                }
            )

    return duplicate_groups


def _build_activity_detail(meta: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    uploads = _load_activity_uploads(meta)
    option_totals: dict[str, int] = {}
    candidate_totals: dict[str, dict[str, int]] = {}
    abnormal_type_totals: dict[str, int] = {
        "blank_ballot": 0,
        "multiple_marks": 0,
        "missing_marks": 0,
        "recognition_failed": 0,
    }
    ballots: list[dict[str, Any]] = []

    for upload in uploads:
        _merge_flat_counts(option_totals, upload.get("option_totals", {}))
        _merge_nested_counts(candidate_totals, upload.get("candidate_totals", {}))
        _merge_flat_counts(abnormal_type_totals, upload.get("abnormal_type_totals", {}))
        ballots.extend(upload.get("ballots", []))

    ballot_count = len(ballots)
    abnormal_ballot_count = sum(1 for ballot in ballots if ballot.get("is_abnormal"))
    duplicate_groups = _annotate_duplicate_ballots(ballots)
    duplicate_ballot_count = sum(1 for ballot in ballots if ballot.get("is_duplicate"))
    valid_vote_count = sum(_safe_int((ballot.get("abnormal_details") or {}).get("valid_vote_count"), 0) for ballot in ballots)
    void_ballot_count = sum(
        1
        for ballot in ballots
        if ballot.get("is_abnormal") and _safe_int((ballot.get("abnormal_details") or {}).get("valid_vote_count"), 0) == 0
    )
    reviewed_ballot_count = sum(1 for ballot in ballots if (ballot.get("review") or {}).get("reviewed"))
    review_completion_rate = round((reviewed_ballot_count / ballot_count) * 100, 1) if ballot_count else 0
    latest_upload = uploads[0] if uploads else None

    return {
        "activity_id": meta["activity_id"],
        "activity_name": meta["activity_name"],
        "created_at": meta["created_at"],
        "updated_at": meta["updated_at"],
        "upload_count": len(uploads),
        "ballot_count": ballot_count,
        "valid_vote_count": valid_vote_count,
        "abnormal_ballot_count": abnormal_ballot_count,
        "void_ballot_count": void_ballot_count,
        "reviewed_ballot_count": reviewed_ballot_count,
        "review_completion_rate": review_completion_rate,
        "duplicate_ballot_count": duplicate_ballot_count,
        "duplicate_groups": duplicate_groups,
        "recognition_mode": meta.get("recognition_mode", "vision_model"),
        "template": meta.get("template") or _normalize_activity_template({}),
        "option_totals": option_totals,
        "candidate_totals": candidate_totals,
        "abnormal_type_totals": abnormal_type_totals,
        "ballots": ballots,
        "uploads": [
            {
                "upload_id": upload["upload_id"],
                "created_at": upload["created_at"],
                "ballot_count": upload["ballot_count"],
                "abnormal_ballot_count": upload["abnormal_ballot_count"],
            }
            for upload in uploads
        ],
        "latest_upload": latest_upload,
        "mobile_entry_url": _absolute_or_relative_mobile_url(request, meta["activity_id"]),
    }


def _refresh_activity_meta(activity_id: str) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
        detail = _build_activity_detail(meta)
        meta["updated_at"] = _iso_now()
        meta["upload_count"] = detail["upload_count"]
        meta["ballot_count"] = detail["ballot_count"]
        meta["abnormal_ballot_count"] = detail["abnormal_ballot_count"]
        _sort_activities(state["activities"])
        _save_state(state)
        return meta


def _build_overview(request: Request | None = None) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        activities = state.get("activities", [])
        latest_activity_id = state.get("latest_activity_id")
        current_detail = None
        if latest_activity_id:
            current_meta = _find_activity_meta(state, latest_activity_id)
            if current_meta is not None:
                current_detail = _build_activity_detail(current_meta, request=request)

        return {
            "latest_activity_id": latest_activity_id,
            "activity_count": len(activities),
            "ballot_count": sum(int(item.get("ballot_count") or 0) for item in activities),
            "abnormal_ballot_count": sum(int(item.get("abnormal_ballot_count") or 0) for item in activities),
            "activities": activities,
            "activity_templates": state.get("activity_templates", []),
            "current_activity": current_detail,
        }


def _save_upload_files(activity_id: str, files: list[UploadFile]) -> tuple[str, Path]:
    upload_id = _new_id("upl_")
    target_dir = _upload_dir(activity_id, upload_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    existing_names: set[str] = set()
    saved_count = 0
    try:
        for index, upload_file in enumerate(files, start=1):
            if not upload_file.filename:
                continue
            filename = _sanitize_filename(upload_file.filename, index, existing_names)
            content = upload_file.file.read()
            if not content:
                continue
            (target_dir / filename).write_bytes(content)
            saved_count += 1
    except Exception:
        _safe_remove_path(target_dir)
        raise

    if saved_count == 0:
        _safe_remove_path(target_dir)
        raise HTTPException(status_code=400, detail="没有收到可识别的图片文件。")
    return upload_id, target_dir


def _serialize_new_upload_result(activity_id: str, upload_id: str, result: Any) -> dict[str, Any]:
    payload = result.to_dict()
    created_at = _iso_now()
    ballots = []
    for index, ballot in enumerate(payload.get("ballots", []), start=1):
        image_path = ballot.get("image_path")
        file_name = Path(image_path).name if image_path else f"ballot_{index:03d}.png"
        ballot["ballot_id"] = f"{upload_id}_{index:03d}"
        ballot["file_name"] = file_name
        ballot["original_url"] = _path_to_data_url(image_path) or ""
        ballot["review"] = {
            "reviewed": False,
            "comment": "",
            "updated_at": None,
        }
        ballots.append(ballot)

    payload.update(
        {
            "upload_id": upload_id,
            "activity_id": activity_id,
            "created_at": created_at,
            "recognition_mode": "vision_model",
            "ballots": ballots,
        }
    )
    state = _load_state()
    meta = _find_activity_meta(state, activity_id) or _normalize_activity_meta({"activity_id": activity_id})
    return _recalculate_upload_payload(payload, meta)


def _run_upload_recognition(activity_id: str, upload_id: str) -> dict[str, Any]:
    result_path = _upload_result_path(activity_id, upload_id)
    if result_path.exists():
        raw = _read_json(result_path, {})
        with STATE_LOCK:
            state = _load_state()
            meta = _require_activity_meta(state, activity_id)
        return _normalize_upload_payload(raw, result_path, meta)

    upload_dir = _upload_dir(activity_id, upload_id)
    result = recognizer.recognize_directory(upload_dir)
    serialized = _serialize_new_upload_result(activity_id, upload_id, result)
    _write_json(result_path, serialized)
    _refresh_activity_meta(activity_id)
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
    return _normalize_upload_payload(serialized, result_path, meta)


def _update_ballot_review(activity_id: str, ballot_id: str, reviewed: bool, comment: str) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
        result_paths = _iter_activity_result_paths(meta)

    updated_at = _iso_now()
    for path in result_paths:
        raw = _read_json(path, {})
        ballots = raw.get("ballots") or []
        for index, ballot in enumerate(ballots):
            current_ballot_id = ballot.get("ballot_id") or f"{raw.get('upload_id') or raw.get('batch_id') or path.parent.name}_{index + 1:03d}"
            if current_ballot_id != ballot_id:
                continue

            ballot["ballot_id"] = current_ballot_id
            ballot["review"] = {
                "reviewed": reviewed,
                "comment": comment,
                "updated_at": updated_at,
            }
            raw["ballots"] = ballots
            _write_json(path, raw)
            normalized_upload = _normalize_upload_payload(raw, path, meta)
            updated_ballot = next((item for item in normalized_upload["ballots"] if item["ballot_id"] == ballot_id), None)
            if updated_ballot is None:
                raise HTTPException(status_code=500, detail="复核结果保存后读取失败。")

            refreshed_meta = _refresh_activity_meta(activity_id)
            detail = _build_activity_detail(refreshed_meta)
            return {
                "ballot": updated_ballot,
                "activity": detail,
            }

    raise HTTPException(status_code=404, detail="未找到对应的投票单。")


def _update_ballot_correction(
    activity_id: str,
    ballot_id: str,
    rows: list[dict[str, Any]],
    reviewed: bool,
    comment: str,
) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
        result_paths = _iter_activity_result_paths(meta)

    updated_at = _iso_now()
    corrections_by_row = {_safe_int(item.get("row_index"), 0): item for item in rows}
    for path in result_paths:
        raw = _read_json(path, {})
        ballots = raw.get("ballots") or []
        for index, ballot in enumerate(ballots):
            current_ballot_id = ballot.get("ballot_id") or f"{raw.get('upload_id') or raw.get('batch_id') or path.parent.name}_{index + 1:03d}"
            if current_ballot_id != ballot_id:
                continue

            ballot["ballot_id"] = current_ballot_id
            for row in ballot.get("rows") or []:
                row_index = _safe_int(row.get("row_index"), 0)
                correction = corrections_by_row.get(row_index)
                if not correction:
                    continue
                candidate_name = str(correction.get("candidate_name") or row.get("candidate_name") or "").strip()
                selected_option = correction.get("selected_option")
                issue = str(correction.get("issue") or "").strip()
                row["candidate_name"] = candidate_name or str(row.get("candidate_name") or "").strip()
                row["selected_option"] = str(selected_option).strip() if selected_option is not None and str(selected_option).strip() else None
                row["issue"] = issue or None
                row["is_ambiguous"] = bool(issue)
                row["correction"] = {
                    "candidate_name": row["candidate_name"],
                    "selected_option": row["selected_option"],
                    "issue": row["issue"],
                    "comment": str(correction.get("comment") or "").strip(),
                    "updated_at": updated_at,
                }

            ballot["review"] = {
                "reviewed": reviewed,
                "comment": comment,
                "updated_at": updated_at,
            }
            raw["ballots"] = ballots
            raw = _recalculate_upload_payload(raw, meta)
            _write_json(path, raw)
            normalized_upload = _normalize_upload_payload(raw, path, meta)
            updated_ballot = next((item for item in normalized_upload["ballots"] if item["ballot_id"] == ballot_id), None)
            if updated_ballot is None:
                raise HTTPException(status_code=500, detail="校正结果保存后读取失败。")

            refreshed_meta = _refresh_activity_meta(activity_id)
            detail = _build_activity_detail(refreshed_meta)
            return {
                "ballot": updated_ballot,
                "activity": detail,
            }

    raise HTTPException(status_code=404, detail="未找到对应的投票单。")


def _delete_ballot(activity_id: str, ballot_id: str) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
        result_paths = _iter_activity_result_paths(meta)

    detail = _build_activity_detail(meta)
    current_ballot = next((item for item in detail.get("ballots", []) if item.get("ballot_id") == ballot_id), None)
    if current_ballot is None:
        raise HTTPException(status_code=404, detail="未找到对应的投票单。")
    if not current_ballot.get("is_duplicate"):
        raise HTTPException(status_code=400, detail="只能删除后续重复票，首次出现的原始票会保留。")

    for path in result_paths:
        raw = _read_json(path, {})
        ballots = raw.get("ballots") or []
        kept_ballots = []
        deleted_ballot: dict[str, Any] | None = None
        for index, ballot in enumerate(ballots):
            current_ballot_id = ballot.get("ballot_id") or f"{raw.get('upload_id') or raw.get('batch_id') or path.parent.name}_{index + 1:03d}"
            if current_ballot_id == ballot_id:
                ballot["ballot_id"] = current_ballot_id
                deleted_ballot = ballot
                continue
            kept_ballots.append(ballot)

        if deleted_ballot is None:
            continue

        image_path = deleted_ballot.get("image_path")
        if image_path:
            _safe_remove_path(Path(image_path))

        raw["ballots"] = kept_ballots
        raw = _recalculate_upload_payload(raw, meta)
        _write_json(path, raw)
        refreshed_meta = _refresh_activity_meta(activity_id)
        detail = _build_activity_detail(refreshed_meta)
        return {
            "deleted_ballot_id": ballot_id,
            "activity": detail,
        }

    raise HTTPException(status_code=404, detail="未找到对应的投票单。")


def _build_activity_csv_export(activity: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["活动总览"])
    writer.writerow(["活动名称", activity.get("activity_name", "")])
    writer.writerow(["投票单数量", activity.get("ballot_count", 0)])
    writer.writerow(["异常票数量", activity.get("abnormal_ballot_count", 0)])
    writer.writerow([])

    writer.writerow(["选项汇总"])
    writer.writerow(["选项", "票数"])
    for option, count in (activity.get("option_totals") or {}).items():
        writer.writerow([option, count])
    writer.writerow([])

    writer.writerow(["候选人明细"])
    option_names = list((activity.get("option_totals") or {}).keys())
    writer.writerow(["候选人", *option_names, "总计"])
    for candidate, counts in (activity.get("candidate_totals") or {}).items():
        total = sum(int(value or 0) for value in counts.values())
        writer.writerow([candidate, *[counts.get(option, 0) for option in option_names], total])
    writer.writerow([])

    writer.writerow(["投票单明细"])
    writer.writerow(["投票单ID", "文件名", "候选人", "选择", "异常类型", "是否复核", "复核备注"])
    for ballot in activity.get("ballots") or []:
        review = ballot.get("review") or {}
        for row in ballot.get("rows") or [{}]:
            selected = _option_label(ballot, row.get("selected_option")) or ""
            writer.writerow(
                [
                    ballot.get("ballot_id", ""),
                    ballot.get("file_name", ""),
                    row.get("candidate_name", ""),
                    selected,
                    row.get("issue") or "",
                    "是" if review.get("reviewed") else "否",
                    review.get("comment") or "",
                ]
            )
    writer.writerow([])

    writer.writerow(["异常票清单"])
    writer.writerow(["投票单ID", "文件名", "异常类型", "复核备注"])
    for ballot in activity.get("ballots") or []:
        if ballot.get("is_abnormal"):
            review = ballot.get("review") or {}
            writer.writerow(
                [
                    ballot.get("ballot_id", ""),
                    ballot.get("file_name", ""),
                    " / ".join(str(item) for item in ballot.get("abnormal_types", [])),
                    review.get("comment") or "",
                ]
            )

    return "\ufeff" + buffer.getvalue()


def _xlsx_column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _worksheet_xml(rows: list[list[Any]]) -> str:
    body = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cell_ref = f"{_xlsx_column_name(col_index)}{row_index}"
            text = xml_escape(str(value if value is not None else ""))
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        body.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def _build_activity_xlsx_export(activity: dict[str, Any]) -> bytes:
    csv_text = _build_activity_csv_export(activity).lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(csv_text)))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        workbook.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>",
        )
        workbook.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        workbook.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="投票结果" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        workbook.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>",
        )
        workbook.writestr("xl/worksheets/sheet1.xml", _worksheet_xml(rows))
    return output.getvalue()


