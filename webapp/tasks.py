from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from .storage import (
    STATE_LOCK,
    _build_activity_detail,
    _iso_now,
    _load_state,
    _require_activity_meta,
    _run_upload_recognition,
)


TASK_LOCK = threading.Lock()
TASKS: dict[str, dict[str, Any]] = {}


def _create_task(message: str) -> str:
    task_id = uuid4().hex
    with TASK_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "status": "running",
            "message": message,
            "created_at": _iso_now(),
        }
    return task_id


def _finish_task(task_id: str, status: str, **extra: Any) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id, {"task_id": task_id})
        task["status"] = status
        task.update(extra)
        TASKS[task_id] = task


def _start_upload_task(activity_id: str, upload_id: str) -> str:
    task_id = _create_task("正在调用视觉模型识别投票单，请稍候...")

    def runner() -> None:
        try:
            _run_upload_recognition(activity_id, upload_id)
            with STATE_LOCK:
                state = _load_state()
                meta = _require_activity_meta(state, activity_id)
            detail = _build_activity_detail(meta)
            _finish_task(task_id, "completed", message="识别完成。", result=detail)
        except Exception as exc:
            _finish_task(task_id, "failed", message="识别失败。", error=str(exc))

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return task_id


