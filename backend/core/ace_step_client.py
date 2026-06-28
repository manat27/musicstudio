"""
ACE-Step Client — wraps ALL pipeline tasks
Tasks supported:
  text2music    → สร้างเพลงใหม่จาก prompt + lyrics
  audio2audio   → เปลี่ยนสไตล์เพลง (voice cover)
  retake        → สร้างใหม่อีกครั้ง variation เดิม
  repaint       → แก้เฉพาะช่วงเวลาในเพลง
  edit          → เปลี่ยน tag/lyrics โดยรักษา melody (Suno-style remix)
"""

import asyncio
import logging
import os
import subprocess
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

ACE_STEP_API = os.getenv("ACE_STEP_API_URL", "http://100.80.64.28:8001")

MIME_MAP = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg",
}


async def _is_online() -> bool:
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{ACE_STEP_API}/health")
            return r.status_code == 200
    except Exception:
        return False


def _read_audio(path: Path) -> tuple[bytes, str]:
    mime = MIME_MAP.get(path.suffix.lower(), "audio/mpeg")
    return path.read_bytes(), mime


async def _post_generate(payload: dict, audio_path: Path | None = None) -> bytes | None:
    """POST to /generate — handles both JSON and multipart."""
    async with httpx.AsyncClient(timeout=240.0) as client:
        if audio_path and audio_path.exists():
            data, mime = _read_audio(audio_path)
            files = {"audio_file": (audio_path.name, data, mime)}
            resp = await client.post(f"{ACE_STEP_API}/generate", data=payload, files=files)
        else:
            resp = await client.post(f"{ACE_STEP_API}/generate", data=payload)

        if resp.status_code != 200:
            logger.error(f"ACE-Step /generate {resp.status_code}: {resp.text[:300]}")
            return None

        ct = resp.headers.get("content-type", "")
        if not any(t in ct for t in ("audio/", "application/octet-stream")):
            logger.error(f"Non-audio response: {ct} — {resp.text[:200]}")
            return None

        if len(resp.content) < 2048:
            logger.error(f"Response too small ({len(resp.content)}B)")
            return None

        return resp.content


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

async def text2music(
    prompt: str,
    lyrics: str,
    duration: float,
    output_path: Path,
    infer_step: int = 60,
    guidance_scale: float = 15.0,
    seed: int = -1,
) -> Path | None:
    """สร้างเพลงใหม่จาก prompt + เนื้อร้อง (Suno-style Create)"""
    payload = {
        "audio_duration": duration,
        "prompt": prompt,
        "lyrics": lyrics,
        "infer_step": infer_step,
        "guidance_scale": guidance_scale,
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "actual_seeds": [seed] if seed >= 0 else [-1],
        "guidance_interval": 0.5,
        "guidance_interval_decay": 0.0,
        "min_guidance_scale": 3.0,
        "use_erg_tag": True,
        "use_erg_lyric": True,
        "use_erg_diffusion": True,
        "audio2audio_enable": False,
        "task": "text2music",
    }
    audio = await _post_generate(payload)
    if audio:
        output_path.write_bytes(audio)
        logger.info(f"✅ text2music → {output_path.name} ({len(audio)//1024}KB)")
        return output_path
    return None


async def audio2audio(
    src_audio: Path,
    prompt: str,
    output_path: Path,
    ref_audio_strength: float = 0.85,
    lyrics: str = "",
    infer_step: int = 20,
    guidance_scale: float = 5.0,
    seed: int = 42,
) -> Path | None:
    """เปลี่ยนเสียง/สไตล์โดยใช้เพลงต้นฉบับเป็น reference (Voice Cover)"""
    payload = {
        "audio_duration": -1,
        "prompt": prompt,
        "lyrics": lyrics,
        "infer_step": infer_step,
        "guidance_scale": guidance_scale,
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "actual_seeds": [seed],
        "guidance_interval": 0.5,
        "guidance_interval_decay": 0.0,
        "min_guidance_scale": 3.0,
        "use_erg_tag": True,
        "use_erg_lyric": True,
        "use_erg_diffusion": True,
        "audio2audio_enable": True,
        "ref_audio_strength": ref_audio_strength,
        "task": "audio2audio",
    }
    audio = await _post_generate(payload, audio_path=src_audio)
    if audio:
        output_path.write_bytes(audio)
        logger.info(f"✅ audio2audio → {output_path.name} ({len(audio)//1024}KB)")
        return output_path
    return None


async def retake(
    src_audio: Path,
    prompt: str,
    output_path: Path,
    variance: float = 0.5,
    seed: int = -1,
    lyrics: str = "",
) -> Path | None:
    """สร้าง variation ใหม่ของเพลงเดิม รักษาโครงสร้างแต่เปลี่ยนรายละเอียด"""
    payload = {
        "audio_duration": -1,
        "prompt": prompt,
        "lyrics": lyrics,
        "infer_step": 30,
        "guidance_scale": 7.0,
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "actual_seeds": [seed] if seed >= 0 else [-1],
        "guidance_interval": 0.5,
        "guidance_interval_decay": 0.0,
        "min_guidance_scale": 3.0,
        "use_erg_tag": True,
        "use_erg_lyric": True,
        "use_erg_diffusion": True,
        "audio2audio_enable": True,
        "ref_audio_strength": variance,
        "task": "retake",
        "retake_variance": variance,
    }
    audio = await _post_generate(payload, audio_path=src_audio)
    if audio:
        output_path.write_bytes(audio)
        logger.info(f"✅ retake → {output_path.name} ({len(audio)//1024}KB)")
        return output_path
    return None


async def repaint(
    src_audio: Path,
    prompt: str,
    output_path: Path,
    repaint_start: float,
    repaint_end: float,
    lyrics: str = "",
    infer_step: int = 30,
) -> Path | None:
    """แก้ไขเฉพาะช่วงเวลาในเพลง เช่น แก้เฉพาะ chorus หรือ bridge"""
    payload = {
        "audio_duration": -1,
        "prompt": prompt,
        "lyrics": lyrics,
        "infer_step": infer_step,
        "guidance_scale": 7.0,
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "actual_seeds": [42],
        "guidance_interval": 0.5,
        "guidance_interval_decay": 0.0,
        "min_guidance_scale": 3.0,
        "use_erg_tag": True,
        "use_erg_lyric": True,
        "use_erg_diffusion": True,
        "audio2audio_enable": True,
        "ref_audio_strength": 0.7,
        "task": "repaint",
        "repaint_start": repaint_start,
        "repaint_end": repaint_end,
    }
    audio = await _post_generate(payload, audio_path=src_audio)
    if audio:
        output_path.write_bytes(audio)
        logger.info(f"✅ repaint [{repaint_start}s-{repaint_end}s] → {output_path.name}")
        return output_path
    return None


async def edit_song(
    src_audio: Path,
    new_prompt: str,
    output_path: Path,
    new_lyrics: str = "",
    edit_n_min: float = 0.0,
    edit_n_max: float = 1.0,
    mode: str = "only_lyrics",   # "only_lyrics" = รักษา melody | "remix" = เปลี่ยน melody
    infer_step: int = 30,
) -> Path | None:
    """
    Edit เพลงด้วย tag/lyrics ใหม่ (Suno Covers / Style Transfer)
    mode='only_lyrics' → เปลี่ยนเนื้อร้องอย่างเดียว รักษา melody เดิม
    mode='remix'       → เปลี่ยนทั้ง tag และ melody
    """
    payload = {
        "audio_duration": -1,
        "prompt": new_prompt,
        "lyrics": new_lyrics,
        "infer_step": infer_step,
        "guidance_scale": 7.0 if mode == "remix" else 5.0,
        "scheduler_type": "euler",
        "cfg_type": "apg",
        "omega_scale": 10.0,
        "actual_seeds": [42],
        "guidance_interval": 0.5,
        "guidance_interval_decay": 0.0,
        "min_guidance_scale": 3.0,
        "use_erg_tag": True,
        "use_erg_lyric": True,
        "use_erg_diffusion": True,
        "audio2audio_enable": True,
        "ref_audio_strength": 0.6 if mode == "remix" else 0.9,
        "task": "edit",
        "edit_target_prompt": new_prompt,
        "edit_target_lyrics": new_lyrics,
        "edit_n_min": edit_n_min,
        "edit_n_max": edit_n_max,
    }
    audio = await _post_generate(payload, audio_path=src_audio)
    if audio:
        output_path.write_bytes(audio)
        logger.info(f"✅ edit ({mode}) → {output_path.name}")
        return output_path
    return None


async def get_audio_info(audio_path: Path) -> dict:
    """Get duration and basic info via FFprobe."""
    def _probe(path):
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            return {
                "duration": float(fmt.get("duration", 0)),
                "size_mb": round(int(fmt.get("size", 0)) / 1024 / 1024, 2),
                "format": fmt.get("format_name", ""),
            }
        return {"duration": 0, "size_mb": 0, "format": "unknown"}

    return await asyncio.to_thread(_probe, audio_path)
