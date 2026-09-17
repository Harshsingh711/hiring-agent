"""Local website for scoring resumes with the hiring-agent pipeline."""

import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Dict

if sys.platform == "win32":
    os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from roles import list_available_roles, load_role
from score import main as score_resume

ROOT = Path(__file__).parent
UPLOADS = ROOT / "uploads"
WEB = ROOT / "web"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

UPLOADS.mkdir(exist_ok=True)

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


def _run_job(job_id: str, pdf_path: str, role_name: str):
    def progress(event):
        _update_job(
            job_id,
            status="running",
            stage=event.get("stage"),
            message=event.get("message"),
            percent=int(event.get("percent") or 0),
        )

    try:
        role = load_role(role_name)
        with _run_lock:
            report = score_resume(
                pdf_path, role, progress=progress, write_csv=False
            )
        if not report:
            _update_job(
                job_id,
                status="error",
                message="Could not read this PDF as a resume.",
                percent=100,
            )
            return
        _update_job(
            job_id,
            status="done",
            result=report,
            percent=100,
            message="Report ready.",
            stage="done",
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="error",
            message=str(exc),
            percent=100,
            stage="error",
        )
    finally:
        try:
            Path(pdf_path).unlink(missing_ok=True)
        except OSError:
            pass


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
    if role not in list_available_roles():
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'.")

    filename = (file.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF resume.")

    data = await file.read()
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
            "filename": file.filename,
        }

    thread = threading.Thread(
        target=_run_job, args=(job_id, str(pdf_path), role), daemon=True
    )
    thread.start()
    return {"job_id": job_id}


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
