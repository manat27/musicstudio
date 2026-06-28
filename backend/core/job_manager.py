"""
Job Manager - Async queue + SQLite persistence
Phase 2: + batch_id field, batch table, list_jobs_by_batch, save/get/list batch
"""

import asyncio
import logging
import uuid
import sqlite3
import json
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("jobs.db")


class JobStatus(str, Enum):
    QUEUED     = "queued"
    SEPARATING = "separating"
    CONVERTING = "converting"
    MIXING     = "mixing"
    EXPORTING  = "exporting"
    DONE       = "done"
    ERROR      = "error"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    stage_label: str = "รอคิว..."
    input_file: str = ""
    mode: str = "vc"
    voice_model: str = "male_thai"
    style_prompt: str = ""
    lyrics: str = ""
    music_caption: str = ""
    gpu_id: int = 0
    pitch: int = 0
    reverb: float = 0.2
    mix_blend: float = 0.8
    enhance_level: float = 0.3
    batch_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None
    outputs: Dict[str, str] = field(default_factory=dict)
    filename: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "stage_label": self.stage_label,
            "mode": self.mode,
            "voice_model": self.voice_model,
            "style_prompt": self.style_prompt,
            "lyrics": self.lyrics,
            "music_caption": self.music_caption,
            "pitch": self.pitch,
            "enhance_level": self.enhance_level,
            "batch_id": self.batch_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "outputs": self.outputs,
            "filename": self.filename,
        }

    def update(self, status: JobStatus, progress: int, label: str):
        self.status = status
        self.progress = progress
        self.stage_label = label
        self.updated_at = datetime.now().isoformat()
        logger.info(f"[Job {self.id[:8]}] {label} ({progress}%)")
        job_manager.save_job(self)


class JobManager:
    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._processor: Optional[Callable] = None
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT,
                    progress INTEGER,
                    stage_label TEXT,
                    input_file TEXT,
                    mode TEXT DEFAULT 'vc',
                    voice_model TEXT,
                    style_prompt TEXT DEFAULT '',
                    lyrics TEXT DEFAULT '',
                    music_caption TEXT DEFAULT '',
                    gpu_id INTEGER DEFAULT 0,
                    pitch INTEGER,
                    reverb REAL,
                    mix_blend REAL,
                    enhance_level REAL DEFAULT 0.3,
                    batch_id TEXT DEFAULT NULL,
                    created_at TEXT,
                    updated_at TEXT,
                    error TEXT,
                    outputs TEXT,
                    filename TEXT DEFAULT ''
                )
            """)
            # Add batch_id column if upgrading from Phase 1
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT DEFAULT NULL")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN enhance_level REAL DEFAULT 0.3")
            except sqlite3.OperationalError:
                pass  # already exists

            conn.execute("""
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    total INTEGER DEFAULT 0,
                    done INTEGER DEFAULT 0,
                    failed INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running',
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.commit()

    def save_job(self, job: Job):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO jobs
                (id, status, progress, stage_label, input_file, mode, voice_model,
                 style_prompt, lyrics, music_caption, gpu_id, pitch, reverb,
                 mix_blend, enhance_level, batch_id, created_at, updated_at, error, outputs, filename)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                job.id, job.status, job.progress, job.stage_label, job.input_file,
                job.mode, job.voice_model, job.style_prompt, job.lyrics,
                job.music_caption, job.gpu_id, job.pitch, job.reverb,
                job.mix_blend, job.enhance_level, job.batch_id, job.created_at, job.updated_at,
                job.error, json.dumps(job.outputs), job.filename,
            ))
            conn.commit()

    def get_job(self, job_id: str) -> Optional[Job]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
            row = cur.fetchone()
            if row:
                d = dict(row)
                d["outputs"] = json.loads(d.get("outputs") or "{}")
                return Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__})
        return None

    def list_jobs(self, limit: int = 50) -> List[Job]:
        jobs = []
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
            for row in cur.fetchall():
                d = dict(row)
                d["outputs"] = json.loads(d.get("outputs") or "{}")
                jobs.append(Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__}))
        return jobs

    def list_jobs_by_batch(self, batch_id: str) -> List[Job]:
        jobs = []
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM jobs WHERE batch_id=? ORDER BY created_at ASC", (batch_id,)
            )
            for row in cur.fetchall():
                d = dict(row)
                d["outputs"] = json.loads(d.get("outputs") or "{}")
                jobs.append(Job(**{k: v for k, v in d.items() if k in Job.__dataclass_fields__}))
        return jobs

    def delete_job(self, job_id: str):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()

    # ── Batch ─────────────────────────────────────────────────────────────────
    def save_batch(self, batch: dict):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO batches
                (id, name, total, done, failed, status, created_at, updated_at)
                VALUES (:id,:name,:total,:done,:failed,:status,:created_at,:updated_at)
            """, batch)
            conn.commit()

    def get_batch(self, batch_id: str) -> Optional[dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_batches(self, limit: int = 20) -> List[dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute("SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ── Queue & worker ────────────────────────────────────────────────────────
    async def start(self):
        # Re-enqueue unfinished jobs on restart
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id FROM jobs WHERE status NOT IN ('done','error') ORDER BY created_at ASC"
            )
            requeued = 0
            for row in cur.fetchall():
                await self._queue.put(row["id"])
                requeued += 1
        if requeued:
            logger.info(f"♻️ Re-enqueued {requeued} unfinished jobs")
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("✅ Job manager started (Persistent + Batch)")

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()

    def set_processor(self, fn: Callable):
        self._processor = fn

    def create_job(self, **kwargs) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, **kwargs)
        self.save_job(job)
        return job

    async def enqueue(self, job: Job):
        await self._queue.put(job.id)
        logger.info(f"[Job {job.id[:8]}] Enqueued (q={self._queue.qsize()})")

    async def _worker(self):
        logger.info("👷 Worker ready")
        while True:
            try:
                job_id = await self._queue.get()
                job = self.get_job(job_id)
                if job and self._processor:
                    try:
                        await self._processor(job)
                    except Exception as e:
                        job.status = JobStatus.ERROR
                        job.error = str(e)
                        job.stage_label = f"Error: {str(e)[:80]}"
                        job.updated_at = datetime.now().isoformat()
                        self.save_job(job)
                        logger.error(f"[Job {job_id[:8]}] Failed: {e}", exc_info=True)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)


job_manager = JobManager()
