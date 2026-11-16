from __future__ import annotations

import io
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .schemas import ActivityCreateRequest, ActivityDeleteRequest, BallotCorrectionRequest, ReviewRequest
from .storage import (
    DATA_DIR,
    FRONTEND_DIR,
    LEGACY_RESULTS_DIR,
    LEGACY_UPLOADS_DIR,
    PUBLIC_BASE_URL,
    STATE_LOCK,
    _activity_dir,
    _build_activity_csv_export,
    _build_activity_detail,
    _build_overview,
    _build_activity_xlsx_export,
    _ensure_storage,
    _iso_now,
    _load_state,
    _new_id,
    _normalize_activity_meta,
    _refresh_activity_meta,
    _require_activity_meta,
    _run_upload_recognition,
    _safe_remove_path,
    _save_state,
    _save_upload_files,
    _remember_activity_template,
    _delete_ballot,
    _update_ballot_correction,
    _update_ballot_review,
)
from .tasks import TASKS, TASK_LOCK, _start_upload_task


app = FastAPI(title="Ballot Recognition Web App")


@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.on_event("startup")
def _startup() -> None:
    _ensure_storage()
    with STATE_LOCK:
        _save_state(_load_state())


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/m/activities/{activity_id}", name="mobile_activity_page")
async def mobile_activity_page(activity_id: str) -> FileResponse:
    with STATE_LOCK:
        state = _load_state()
        _require_activity_meta(state, activity_id)
    return FileResponse(FRONTEND_DIR / "mobile_activity.html")


@app.get("/api/overview")
async def get_overview(request: Request) -> dict[str, Any]:
    return _build_overview(request)


@app.get("/api/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "timestamp": _iso_now(),
        "data_dir": str(DATA_DIR),
        "public_base_url": PUBLIC_BASE_URL or None,
    }


@app.get("/api/activities")
async def get_activities() -> list[dict[str, Any]]:
    with STATE_LOCK:
        return _load_state().get("activities", [])


@app.post("/api/activities")
async def create_activity(payload: ActivityCreateRequest, request: Request) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        activity_id = _new_id("act_")
        now = _iso_now()
        meta = _normalize_activity_meta(
            {
                "activity_id": activity_id,
                "activity_name": payload.activity_name,
                "created_at": now,
                "updated_at": now,
                "upload_count": 0,
                "ballot_count": 0,
                "abnormal_ballot_count": 0,
                "recognition_mode": "vision_model",
                "legacy_batch_ids": [],
                "template": {
                    "candidates": payload.candidates,
                    "options": payload.options,
                    "invalid_mark_rules": payload.invalid_mark_rules,
                },
            }
        )
        state["activities"].append(meta)
        state["latest_activity_id"] = activity_id
        _remember_activity_template(state, meta)
        _save_state(state)

    return {
        "activity": _build_activity_detail(meta, request=request),
        "overview": _build_overview(request),
    }


@app.get("/api/activities/{activity_id}")
async def get_activity(activity_id: str, request: Request) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
    return _build_activity_detail(meta, request=request)


@app.post("/api/activities/{activity_id}/activate")
async def activate_activity(activity_id: str, request: Request) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        _require_activity_meta(state, activity_id)
        state["latest_activity_id"] = activity_id
        _save_state(state)
    return _build_overview(request)


@app.post("/api/current/reset")
async def reset_current_activity() -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        state["latest_activity_id"] = None
        _save_state(state)
    return {"ok": True}


@app.post("/api/activities/delete")
async def delete_activities(payload: ActivityDeleteRequest, request: Request) -> dict[str, Any]:
    activity_ids = {str(item).strip() for item in payload.activity_ids if str(item).strip()}
    if not activity_ids:
        raise HTTPException(status_code=400, detail="请选择要删除的活动。")

    deleted_count = 0
    with STATE_LOCK:
        state = _load_state()
        kept: list[dict[str, Any]] = []
        for meta in state.get("activities", []):
            if meta.get("activity_id") not in activity_ids:
                kept.append(meta)
                continue

            deleted_count += 1
            _safe_remove_path(_activity_dir(meta["activity_id"]))
            for batch_id in meta.get("legacy_batch_ids", []):
                _safe_remove_path(LEGACY_UPLOADS_DIR / batch_id)
                _safe_remove_path(LEGACY_RESULTS_DIR / batch_id)

        state["activities"] = kept
        if state.get("latest_activity_id") in activity_ids:
            state["latest_activity_id"] = kept[0]["activity_id"] if kept else None
        _save_state(state)

    return {
        "deleted_count": deleted_count,
        "overview": _build_overview(request),
    }


@app.post("/api/activities/{activity_id}/upload")
async def upload_to_activity(
    activity_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    async_mode: str = Form("false"),
) -> dict[str, Any]:
    with STATE_LOCK:
        state = _load_state()
        _require_activity_meta(state, activity_id)
        state["latest_activity_id"] = activity_id
        _save_state(state)

    upload_id, _ = _save_upload_files(activity_id, files)
    is_async = str(async_mode).strip().lower() in {"1", "true", "yes", "on"}
    if is_async:
        task_id = _start_upload_task(activity_id, upload_id)
        return {"task_id": task_id, "upload_id": upload_id}

    _run_upload_recognition(activity_id, upload_id)
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
        state["latest_activity_id"] = activity_id
        _save_state(state)
    return _build_activity_detail(meta, request=request)


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    with TASK_LOCK:
        task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return task


@app.post("/api/activities/{activity_id}/ballots/{ballot_id}/review")
async def review_ballot(activity_id: str, ballot_id: str, payload: ReviewRequest) -> dict[str, Any]:
    return _update_ballot_review(activity_id, ballot_id, payload.reviewed, payload.comment)


@app.post("/api/activities/{activity_id}/ballots/{ballot_id}/correction")
async def correct_ballot(activity_id: str, ballot_id: str, payload: BallotCorrectionRequest) -> dict[str, Any]:
    return _update_ballot_correction(
        activity_id,
        ballot_id,
        [row.model_dump() for row in payload.rows],
        payload.reviewed,
        payload.comment,
    )


@app.delete("/api/activities/{activity_id}/ballots/{ballot_id}")
async def delete_ballot(activity_id: str, ballot_id: str) -> dict[str, Any]:
    return _delete_ballot(activity_id, ballot_id)


@app.get("/api/activities/{activity_id}/export.csv")
async def export_activity_csv(activity_id: str) -> StreamingResponse:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
    activity = _build_activity_detail(meta)
    content = _build_activity_csv_export(activity).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{activity_id}.csv"'},
    )


@app.get("/api/activities/{activity_id}/export.xlsx")
async def export_activity_xlsx(activity_id: str) -> StreamingResponse:
    with STATE_LOCK:
        state = _load_state()
        meta = _require_activity_meta(state, activity_id)
    activity = _build_activity_detail(meta)
    content = _build_activity_xlsx_export(activity)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{activity_id}.xlsx"'},
    )
