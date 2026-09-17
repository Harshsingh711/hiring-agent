"""Website for scoring resumes with the hiring-agent pipeline."""

import json
import os
import queue
import sys
import threading
import uuid
from pathlib import Path
from typing import Dict

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from roles import list_available_roles, load_role
from score import main as score_resume

ROOT = Path(__file__).parent
WEB = ROOT / "web"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def _runtime_dir(name: str) -> Path:
    base = Path("/tmp/hiring-agent") if os.getenv("VERCEL") else ROOT
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return path


UPLOADS = _runtime_dir("uploads")

app = FastAPI(title="Hiring Agent", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB), name="static")

_jobs: Dict[str, dict] = {}
_jobs_lock = threading.Lock()
_run_lock = threading.Lock()


def _update_job(job_id: str, **fields):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)


def _run_job(job_id: str, pdf_path: str, role_name: str, progress=None):
    def on_progress(event):
        payload = {
            "status": "running",
            "stage": event.get("stage"),
            "message": event.get("message"),
            "percent": int(event.get("percent") or 0),
        }
        _update_job(job_id, **payload)
        if progress:
            progress(payload)

    try:
        role = load_role(role_name)
        with _run_lock:
            report = score_resume(
                pdf_path, role, progress=on_progress, write_csv=False
            )
        if not report:
            payload = {
                "status": "error",
                "message": "Could not read this PDF as a resume.",
                "percent": 100,
                "stage": "error",
            }
            _update_job(job_id, **payload)
            if progress:
                progress(payload)
            return
        payload = {
            "status": "done",
            "result": report,
            "percent": 100,
            "message": "Report ready.",
            "stage": "done",
        }
        _update_job(job_id, **payload)
        if progress:
            progress(payload)
    except Exception as exc:
        payload = {
            "status": "error",
            "message": str(exc),
            "percent": 100,
            "stage": "error",
        }
        _update_job(job_id, **payload)
        if progress:
            progress(payload)
    finally:
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except OSError:
            pass


def _save_upload(role: str, filename: str, data: bytes) -> tuple[str, str]:
    if role not in list_available_roles():
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'.")
    if not (filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF resume.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File is over 8 MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="That file does not look like a PDF.")

    job_id = str(uuid.uuid4())
    pdf_path = UPLOADS / f"{job_id}.pdf"
    pdf_path.write_bytes(data)
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "message": "Waiting to start...",
            "percent": 0,
            "result": None,
            "filename": filename,
        }
    return job_id, str(pdf_path)


@app.get("/api/roles")
def roles():
    payload = []
    for name in list_available_roles():
        role = load_role(name)
        payload.append(
            {
                "name": role.name,
                "position_title": role.position_title,
                "categories": [
                    {"key": category.key, "label": category.label, "max": category.max}
                    for category in role.categories
                ],
            }
        )
    return {"roles": payload}


@app.post("/api/evaluate")
async def evaluate(role: str = Form(...), file: UploadFile = File(...)):
    job_id, pdf_path = _save_upload(role, file.filename or "", await file.read())
    events: queue.Queue = queue.Queue()

    def progress(payload):
        events.put(payload)

    def worker():
        _run_job(job_id, pdf_path, role, progress=progress)
        events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
