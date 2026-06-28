"""
API Routes - REST endpoints for AI Cover Studio
Phase 2: + Batch tracking, Queue status, History clear, Voice CRUD
"""

import os
import shutil
import logging
import uuid
import json
import sqlite3
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse

from core.job_manager import job_manager, JobStatus
import core.processor  # registers processor

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
ALLOWED_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
MAX_FILE_MB = 100


# ── Single cover ──────────────────────────────────────────────────────────────

@router.post("/cover/create")
async def create_cover(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    mode: str = Form("full"),
    voice_model: str = Form("male_thai"),
    style_prompt: str = Form(""),
    gpu_id: int = Form(0),
    pitch: int = Form(0),
    reverb: float = Form(0.2),
    mix_blend: float = Form(0.8),
    enhance_level: float = Form(0.3),
):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(400, f"ไม่รองรับไฟล์ {suffix}")
    if file.size and file.size > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"ไฟล์ใหญ่เกิน {MAX_FILE_MB}MB")
    if pitch not in range(-6, 7):
        raise HTTPException(400, "Pitch ต้องอยู่ระหว่าง -6 ถึง +6")

    job = job_manager.create_job(
        mode=mode,
        voice_model=voice_model,
        style_prompt=style_prompt,
        gpu_id=gpu_id,
        pitch=pitch,
        reverb=reverb,
        mix_blend=mix_blend,
        filename=file.filename,
    )

    upload_path = UPLOAD_DIR / f"{job.id}{suffix}"
    upload_path.write_bytes(await file.read())
    job.input_file = str(upload_path)
    job_manager.save_job(job)

    background_tasks.add_task(_enqueue_job, job)
    return JSONResponse({"job_id": job.id, "status": job.status}, status_code=202)


# ── Batch cover ───────────────────────────────────────────────────────────────

@router.post("/cover/batch")
async def create_batch_cover(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    mode: str = Form("vc"),
    voice_model: str = Form("male_thai"),
    style_prompt: str = Form(""),
    gpu_id: int = Form(0),
    pitch: int = Form(0),
    reverb: float = Form(0.2),
    mix_blend: float = Form(0.8),
    batch_name: str = Form(""),
):
    if len(files) > 20:
        raise HTTPException(400, "สูงสุด 20 ไฟล์ต่อ batch")

    batch_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    batch_label = batch_name or f"Batch {datetime.now().strftime('%H:%M:%S')}"

    job_ids = []
    for f in files:
        suffix = Path(f.filename).suffix.lower()
        if suffix not in ALLOWED_EXT:
            continue
        job = job_manager.create_job(
            mode=mode,
            voice_model=voice_model,
            style_prompt=style_prompt,
            gpu_id=gpu_id,
            pitch=pitch,
            reverb=reverb,
            mix_blend=mix_blend,
            filename=f.filename,
            batch_id=batch_id,
        )
        upload_path = UPLOAD_DIR / f"{job.id}{suffix}"
        upload_path.write_bytes(await f.read())
        job.input_file = str(upload_path)
        job_manager.save_job(job)
        background_tasks.add_task(_enqueue_job, job)
        job_ids.append(job.id)

    if not job_ids:
        raise HTTPException(400, "ไม่มีไฟล์ที่รองรับ")

    # Save batch record
    job_manager.save_batch({
        "id": batch_id,
        "name": batch_label,
        "total": len(job_ids),
        "done": 0,
        "failed": 0,
        "status": "running",
        "created_at": now,
        "updated_at": now,
    })

    return JSONResponse({
        "batch_id": batch_id,
        "name": batch_label,
        "job_ids": job_ids,
        "total": len(job_ids),
    }, status_code=202)


async def _enqueue_job(job):
    await job_manager.enqueue(job)


# ── Status & Download ─────────────────────────────────────────────────────────

@router.get("/cover/status/{job_id}")
async def get_status(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, f"ไม่พบ job: {job_id}")
    return job.to_dict()


@router.get("/cover/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str):
    allowed = {"cover.mp3", "cover.wav", "vocal.wav", "instrumental.wav"}
    if filename not in allowed:
        raise HTTPException(400, f"Invalid filename: {filename}")
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.DONE:
        raise HTTPException(400, f"Job not done yet: {job.status}")
    file_path = OUTPUT_DIR / job_id / filename
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {filename}")
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    return FileResponse(str(file_path), media_type=media_type, filename=filename)


# ── Batch tracking ────────────────────────────────────────────────────────────

@router.get("/batch/{batch_id}")
async def get_batch(batch_id: str):
    batch = job_manager.get_batch(batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    jobs = [j.to_dict() for j in job_manager.list_jobs_by_batch(batch_id)]
    # Update batch counters live
    done = sum(1 for j in jobs if j["status"] == "done")
    failed = sum(1 for j in jobs if j["status"] == "error")
    total = batch["total"]
    batch.update(done=done, failed=failed,
                 status="done" if done + failed >= total else "running")
    return {**batch, "jobs": jobs}


@router.get("/batches")
async def list_batches():
    return job_manager.list_batches()


# ── Jobs history ──────────────────────────────────────────────────────────────

@router.get("/jobs")
async def list_jobs(limit: int = 50):
    return [j.to_dict() for j in job_manager.list_jobs(limit=limit)]


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    out_dir = OUTPUT_DIR / job_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    if job.input_file:
        p = Path(job.input_file)
        if p.exists():
            p.unlink(missing_ok=True)
    job_manager.delete_job(job_id)
    return {"deleted": job_id}


@router.delete("/jobs")
async def clear_history():
    """Delete all done/error jobs and their files."""
    jobs = job_manager.list_jobs(limit=1000)
    deleted = 0
    for job in jobs:
        if job.status in ("done", "error"):
            out_dir = OUTPUT_DIR / job.id
            if out_dir.exists():
                shutil.rmtree(out_dir)
            if job.input_file:
                p = Path(job.input_file)
                if p.exists():
                    p.unlink(missing_ok=True)
            job_manager.delete_job(job.id)
            deleted += 1
    return {"deleted": deleted}


# ── Voice models ──────────────────────────────────────────────────────────────

@router.get("/voices")
async def list_voices():
    from core.voice_manager import voice_manager
    return voice_manager.list_voices()


@router.post("/voices")
async def add_voice(
    id: str = Form(...),
    tags: str = Form(...),
    description: str = Form(...),
    icon: str = Form("🎤"),
):
    from core.voice_manager import voice_manager
    existing = [v["id"] for v in voice_manager.list_voices()]
    if id in existing:
        raise HTTPException(400, f"Voice ID '{id}' มีอยู่แล้ว")
    voice_manager.add_voice(id, tags, description, icon)
    return {"status": "success", "id": id}


@router.delete("/voices/{voice_id}")
async def delete_voice(voice_id: str):
    from core.voice_manager import voice_manager
    voice_manager.delete_voice(voice_id)
    return {"deleted": voice_id}


# ── Queue & system ────────────────────────────────────────────────────────────

@router.get("/queue")
async def queue_status():
    active = [j for j in job_manager.list_jobs(limit=200)
              if j.status not in ("done", "error")]
    return {
        "queued": job_manager._queue.qsize(),
        "active": len(active),
        "active_jobs": [j.to_dict() for j in active],
    }


@router.get("/ace-step/status")
async def ace_step_status():
    import httpx
    ace_url = os.getenv("ACE_STEP_API_URL", "http://100.80.64.28:8001")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{ace_url}/health")
            return {"online": r.status_code == 200, "url": ace_url}
    except Exception as e:
        return {"online": False, "url": ace_url, "error": str(e)}


@router.get("/system")
async def system_info():
    import sys, platform
    return {
        "platform": platform.system(),
        "python": sys.version.split()[0],
        "queue_size": job_manager._queue.qsize(),
    }
