"""
Suno-Style Routes — Phase 3 features
  POST /api/create      → text2music (สร้างเพลงใหม่)
  POST /api/cover       → audio2audio (เปลี่ยนสไตล์/เสียง)  
  POST /api/retake      → retake (variation)
  POST /api/repaint     → repaint (แก้ช่วงเวลา)
  POST /api/edit        → edit (เปลี่ยน lyrics/style)
  GET  /api/tasks/{id}  → task status
  GET  /api/ace/info    → ACE-Step capabilities
"""

import asyncio
import logging
import uuid
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse

logger = logging.getLogger(__name__)
router = APIRouter()

OUTPUT_DIR = Path("outputs/tasks")
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path("jobs.db")


# ── Task persistence ──────────────────────────────────────────────────────────
def _init_tasks_table():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ace_tasks (
                id TEXT PRIMARY KEY,
                task_type TEXT,
                status TEXT DEFAULT 'running',
                progress INTEGER DEFAULT 0,
                label TEXT DEFAULT '',
                prompt TEXT DEFAULT '',
                lyrics TEXT DEFAULT '',
                input_file TEXT DEFAULT '',
                output_file TEXT DEFAULT '',
                error TEXT DEFAULT NULL,
                params TEXT DEFAULT '{}',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()

_init_tasks_table()


def save_task(task: dict):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO ace_tasks
            (id,task_type,status,progress,label,prompt,lyrics,
             input_file,output_file,error,params,created_at,updated_at)
            VALUES (:id,:task_type,:status,:progress,:label,:prompt,:lyrics,
                    :input_file,:output_file,:error,:params,:created_at,:updated_at)
        """, {**task, "params": json.dumps(task.get("params", {}))})
        conn.commit()


def get_task(task_id: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM ace_tasks WHERE id=?", (task_id,))
        row = cur.fetchone()
        if row:
            d = dict(row)
            d["params"] = json.loads(d.get("params") or "{}")
            return d
    return None


def list_tasks(limit: int = 30) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM ace_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = cur.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d.get("params") or "{}")
            result.append(d)
        return result


def update_task(task_id: str, **fields):
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k}=:{k}" for k in fields)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"UPDATE ace_tasks SET {set_clause} WHERE id=:id",
            {**fields, "id": task_id}
        )
        conn.commit()


def new_task(task_type: str, prompt: str = "", lyrics: str = "",
             input_file: str = "", **params) -> dict:
    now = datetime.now().isoformat()
    return {
        "id": str(uuid.uuid4()),
        "task_type": task_type,
        "status": "running",
        "progress": 0,
        "label": f"เริ่ม {task_type}...",
        "prompt": prompt,
        "lyrics": lyrics,
        "input_file": input_file,
        "output_file": "",
        "error": None,
        "params": params,
        "created_at": now,
        "updated_at": now,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename).suffix.lower()
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(await file.read())
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/create")
async def create_song(
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    lyrics: str = Form(""),
    duration: float = Form(30.0),
    infer_step: int = Form(60),
    guidance_scale: float = Form(15.0),
    seed: int = Form(-1),
):
    """🎵 สร้างเพลงใหม่จาก prompt + เนื้อร้อง (เหมือน Suno Create)"""
    task = new_task("text2music", prompt=prompt, lyrics=lyrics,
                    duration=duration, infer_step=infer_step,
                    guidance_scale=guidance_scale, seed=seed)
    save_task(task)
    background_tasks.add_task(_run_text2music, task["id"])
    return JSONResponse({"task_id": task["id"], "status": "running"}, status_code=202)


@router.post("/cover")
async def style_cover(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(...),
    lyrics: str = Form(""),
    ref_audio_strength: float = Form(0.85),
    infer_step: int = Form(20),
    guidance_scale: float = Form(5.0),
    seed: int = Form(42),
):
    """🎤 เปลี่ยนสไตล์/เสียงเพลง โดยใช้เพลงต้นฉบับเป็น reference"""
    input_path = await _save_upload(file)
    task = new_task("audio2audio", prompt=prompt, lyrics=lyrics,
                    input_file=str(input_path),
                    ref_audio_strength=ref_audio_strength,
                    infer_step=infer_step,
                    guidance_scale=guidance_scale, seed=seed)
    save_task(task)
    background_tasks.add_task(_run_audio2audio, task["id"])
    return JSONResponse({"task_id": task["id"], "status": "running"}, status_code=202)


@router.post("/retake")
async def retake_song(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(...),
    lyrics: str = Form(""),
    variance: float = Form(0.5),
    seed: int = Form(-1),
):
    """🔄 สร้าง variation ใหม่ของเพลงเดิม (เหมือน Suno Remaster)"""
    input_path = await _save_upload(file)
    task = new_task("retake", prompt=prompt, lyrics=lyrics,
                    input_file=str(input_path),
                    variance=variance, seed=seed)
    save_task(task)
    background_tasks.add_task(_run_retake, task["id"])
    return JSONResponse({"task_id": task["id"], "status": "running"}, status_code=202)


@router.post("/repaint")
async def repaint_song(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    prompt: str = Form(...),
    lyrics: str = Form(""),
    repaint_start: float = Form(0.0),
    repaint_end: float = Form(15.0),
    infer_step: int = Form(30),
):
    """✏️ แก้ไขเฉพาะช่วงเวลาในเพลง (เหมือน Suno Edit Section)"""
    input_path = await _save_upload(file)
    task = new_task("repaint", prompt=prompt, lyrics=lyrics,
                    input_file=str(input_path),
                    repaint_start=repaint_start,
                    repaint_end=repaint_end,
                    infer_step=infer_step)
    save_task(task)
    background_tasks.add_task(_run_repaint, task["id"])
    return JSONResponse({"task_id": task["id"], "status": "running"}, status_code=202)


@router.post("/edit")
async def edit_song(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    new_prompt: str = Form(...),
    new_lyrics: str = Form(""),
    mode: str = Form("only_lyrics"),   # only_lyrics | remix
    infer_step: int = Form(30),
):
    """🎛️ เปลี่ยน lyrics/style โดยรักษา melody (เหมือน Suno Covers)"""
    input_path = await _save_upload(file)
    task = new_task("edit", prompt=new_prompt, lyrics=new_lyrics,
                    input_file=str(input_path),
                    mode=mode, infer_step=infer_step)
    save_task(task)
    background_tasks.add_task(_run_edit, task["id"])
    return JSONResponse({"task_id": task["id"], "status": "running"}, status_code=202)


# ── Task status ───────────────────────────────────────────────────────────────

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    return task


@router.get("/tasks")
async def list_all_tasks(limit: int = 30):
    return list_tasks(limit)


@router.get("/tasks/{task_id}/download")
async def download_task_result(task_id: str):
    task = get_task(task_id)
    if not task or task["status"] != "done":
        raise HTTPException(400, "Task not done")
    path = Path(task["output_file"])
    if not path.exists():
        raise HTTPException(404, "Output file not found")
    return FileResponse(str(path), filename=path.name,
                        media_type="audio/mpeg" if path.suffix==".mp3" else "audio/wav")


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    task = get_task(task_id)
    if task and task["output_file"]:
        p = Path(task["output_file"])
        if p.exists():
            p.unlink()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM ace_tasks WHERE id=?", (task_id,))
        conn.commit()
    return {"deleted": task_id}


@router.get("/ace/info")
async def ace_info():
    """ACE-Step capabilities + connection status"""
    import os
    from core.ace_step_client import _is_online, ACE_STEP_API
    online = await _is_online()
    return {
        "online": online,
        "url": ACE_STEP_API,
        "tasks": {
            "text2music": "สร้างเพลงใหม่จาก prompt + lyrics",
            "audio2audio": "เปลี่ยนสไตล์/เสียงด้วยเพลงต้นฉบับ",
            "retake": "variation ใหม่ของเพลงเดิม",
            "repaint": "แก้ไขเฉพาะช่วงเวลา",
            "edit": "เปลี่ยน lyrics/style รักษา melody",
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND RUNNERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _run_text2music(task_id: str):
    from core.ace_step_client import text2music
    task = get_task(task_id)
    p = task["params"]
    out = OUTPUT_DIR / f"{task_id}.wav"
    try:
        update_task(task_id, progress=10, label="กำลังสร้างเพลง...")
        result = await text2music(
            prompt=task["prompt"], lyrics=task["lyrics"],
            duration=p.get("duration", 30.0),
            output_path=out,
            infer_step=p.get("infer_step", 60),
            guidance_scale=p.get("guidance_scale", 15.0),
            seed=p.get("seed", -1),
        )
        if result:
            update_task(task_id, status="done", progress=100,
                        label="✅ เสร็จสิ้น", output_file=str(out))
        else:
            update_task(task_id, status="error", label="ACE-Step ไม่ตอบสนอง")
    except Exception as e:
        update_task(task_id, status="error", error=str(e), label=f"Error: {e}")
        logger.error(f"[text2music {task_id[:8]}] {e}", exc_info=True)


async def _run_audio2audio(task_id: str):
    from core.ace_step_client import audio2audio
    task = get_task(task_id)
    p = task["params"]
    out = OUTPUT_DIR / f"{task_id}.wav"
    try:
        update_task(task_id, progress=10, label="กำลังแปลงเสียง...")
        result = await audio2audio(
            src_audio=Path(task["input_file"]),
            prompt=task["prompt"], lyrics=task["lyrics"],
            output_path=out,
            ref_audio_strength=p.get("ref_audio_strength", 0.85),
            infer_step=p.get("infer_step", 20),
            guidance_scale=p.get("guidance_scale", 5.0),
            seed=p.get("seed", 42),
        )
        if result:
            update_task(task_id, status="done", progress=100,
                        label="✅ เสร็จสิ้น", output_file=str(out))
        else:
            update_task(task_id, status="error", label="ACE-Step ไม่ตอบสนอง")
    except Exception as e:
        update_task(task_id, status="error", error=str(e), label=f"Error: {e}")
        logger.error(f"[audio2audio {task_id[:8]}] {e}", exc_info=True)


async def _run_retake(task_id: str):
    from core.ace_step_client import retake
    task = get_task(task_id)
    p = task["params"]
    out = OUTPUT_DIR / f"{task_id}.wav"
    try:
        update_task(task_id, progress=10, label="กำลังสร้าง variation...")
        result = await retake(
            src_audio=Path(task["input_file"]),
            prompt=task["prompt"], lyrics=task["lyrics"],
            output_path=out,
            variance=p.get("variance", 0.5),
            seed=p.get("seed", -1),
        )
        if result:
            update_task(task_id, status="done", progress=100,
                        label="✅ เสร็จสิ้น", output_file=str(out))
        else:
            update_task(task_id, status="error", label="ACE-Step ไม่ตอบสนอง")
    except Exception as e:
        update_task(task_id, status="error", error=str(e), label=f"Error: {e}")


async def _run_repaint(task_id: str):
    from core.ace_step_client import repaint
    task = get_task(task_id)
    p = task["params"]
    out = OUTPUT_DIR / f"{task_id}.wav"
    try:
        update_task(task_id, progress=10, label="กำลัง repaint ช่วงที่เลือก...")
        result = await repaint(
            src_audio=Path(task["input_file"]),
            prompt=task["prompt"], lyrics=task["lyrics"],
            output_path=out,
            repaint_start=p.get("repaint_start", 0.0),
            repaint_end=p.get("repaint_end", 15.0),
            infer_step=p.get("infer_step", 30),
        )
        if result:
            update_task(task_id, status="done", progress=100,
                        label="✅ เสร็จสิ้น", output_file=str(out))
        else:
            update_task(task_id, status="error", label="ACE-Step ไม่ตอบสนอง")
    except Exception as e:
        update_task(task_id, status="error", error=str(e), label=f"Error: {e}")


async def _run_edit(task_id: str):
    from core.ace_step_client import edit_song
    task = get_task(task_id)
    p = task["params"]
    out = OUTPUT_DIR / f"{task_id}.wav"
    try:
        update_task(task_id, progress=10, label="กำลัง edit เพลง...")
        result = await edit_song(
            src_audio=Path(task["input_file"]),
            new_prompt=task["prompt"],
            new_lyrics=task["lyrics"],
            output_path=out,
            mode=p.get("mode", "only_lyrics"),
            infer_step=p.get("infer_step", 30),
        )
        if result:
            update_task(task_id, status="done", progress=100,
                        label="✅ เสร็จสิ้น", output_file=str(out))
        else:
            update_task(task_id, status="error", label="ACE-Step ไม่ตอบสนอง")
    except Exception as e:
        update_task(task_id, status="error", error=str(e), label=f"Error: {e}")
