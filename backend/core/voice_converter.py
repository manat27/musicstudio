"""
Voice Converter - ACE-Step API integration
Phase 2 Bug Fixes:
  BUG 1: analyze_audio over-indentation (body never executed)
  BUG 2: asyncio.create_subprocess_exec crashes on Windows → subprocess.run via to_thread
  BUG 8: ref_audio_strength missing → ACE-Step ignored vocal input, generated new song instead
  BUG 9: resp.content saved without checking Content-Type → corrupt audio file on API error
"""

import asyncio
import logging
import httpx
import os
import shutil
import subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from core.voice_manager import voice_manager

logger = logging.getLogger(__name__)

ACE_STEP_API = os.getenv("ACE_STEP_API_URL", "http://100.80.64.28:8001")

# Supported audio MIME types for correct multipart upload
MIME_MAP = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}


async def convert_voice(
    vocal_path: Path,
    output_path: Path,
    voice_model: str = "male_thai",
    pitch_shift: int = 0,
    style_prompt: str = "",
    lyrics: str = "",  # เนื้อร้องที่ดึงมาจาก analyze_audio
) -> Path:
    if not voice_model:
        voice_model = "male_thai"

    preset = voice_manager.get_voice(voice_model)
    if not preset:
        logger.warning(f"Voice model '{voice_model}' not found, using male_thai")
        preset = voice_manager.get_voice("male_thai")

    active_preset = {
        "tags": preset["tags"],
        "description": preset["description"],
    }

    if voice_model == "suno_mode":
        active_preset["tags"] = style_prompt
        active_preset["description"] = "Suno Style Transfer"
    elif style_prompt:
        active_preset["tags"] = f"{preset['tags']}, {style_prompt}"
        active_preset["description"] = f"{preset['description']} + {style_prompt}"

    is_suno = bool(style_prompt)

    try:
        result = await _convert_via_ace_step(
            vocal_path, output_path, active_preset, pitch_shift, is_suno, lyrics
        )
        if result:
            return result
    except Exception as e:
        logger.warning(f"ACE-Step unavailable: {e} — falling back to FFmpeg pitch shift")

    return await _pitch_shift_only(vocal_path, output_path, pitch_shift)


async def _convert_via_ace_step(
    vocal_path: Path,
    output_path: Path,
    preset: dict,
    pitch_shift: int,
    is_suno: bool = False,
    lyrics: str = "",  # เนื้อร้องที่ดึงมาจาก analyze_audio
) -> Path | None:
    async with httpx.AsyncClient(timeout=180.0) as client:

        # 1. Health check
        try:
            health = await client.get(f"{ACE_STEP_API}/health", timeout=5.0)
            if health.status_code != 200:
                logger.warning(f"ACE-Step health check failed: {health.status_code}")
                return None
        except Exception as e:
            logger.warning(f"ACE-Step unreachable: {e}")
            return None

        # 2. Read vocal with correct MIME type (BUG 3 fix)
        suffix = vocal_path.suffix.lower()
        mime_type = MIME_MAP.get(suffix, "audio/mpeg")

        with open(vocal_path, "rb") as f:
            audio_bytes = f.read()

        # 3. Build tags
        tags = preset["tags"]
        if pitch_shift != 0:
            tags += f", pitch shift {pitch_shift:+d} semitones"

        # 4. Payload — include ref_audio_strength for cover/repaint mode (BUG 8 fix)
        payload = {
            "audio_duration": -1,
            "prompt": tags,
            "lyrics": lyrics,  # ใช้เนื้อร้องที่ดึงมา (หรือส่งเข้ามา)
            "infer_step": 20 if is_suno else 8,
            "guidance_scale": 5.0 if is_suno else 3.5,
            "scheduler_type": "euler",
            "cfg_type": "apg",
            "omega_scale": 12.0 if is_suno else 10.0,
            "actual_seeds": [42],
            "ernie_lm_text_encoder_weight": 1.2 if is_suno else 1.0,
            "retokenize": is_suno,
            # ── BUG 8 FIX: ref_audio_strength tells ACE-Step to USE the uploaded audio ──
            # 0.0 = ignore audio completely (pure generation)
            # 1.0 = keep audio exactly as-is
            # 0.75-0.85 = good balance for voice conversion (change voice, keep melody/timing)
            "ref_audio_strength": 0.5 if is_suno else 0.85,
        }

        files = {"audio_file": (vocal_path.name, audio_bytes, mime_type)}

        logger.info(
            f"ACE-Step generate: voice={preset['description']}, "
            f"ref_strength={payload['ref_audio_strength']}, "
            f"steps={payload['infer_step']}, suno={is_suno}"
        )

        resp = await client.post(
            f"{ACE_STEP_API}/generate",
            data=payload,
            files=files,
        )

        if resp.status_code != 200:
            logger.warning(f"ACE-Step returned {resp.status_code}: {resp.text[:300]}")
            return None

        # ── BUG 9 FIX: verify Content-Type before saving ─────────────────────
        content_type = resp.headers.get("content-type", "")
        if not any(t in content_type for t in ("audio/", "application/octet-stream")):
            logger.error(
                f"ACE-Step returned non-audio Content-Type: {content_type}\n"
                f"Body preview: {resp.text[:200]}"
            )
            return None

        if len(resp.content) < 1024:
            logger.error(
                f"ACE-Step response too small ({len(resp.content)} bytes) — likely an error"
            )
            return None

        output_path.write_bytes(resp.content)
        logger.info(f"✅ Voice converted via ACE-Step → {output_path.name} ({len(resp.content)/1024:.0f}KB)")
        return output_path


async def analyze_audio(audio_path: Path) -> dict:
    """
    Use ACE-Step /describe or /transcribe if available.
    BUG 1 FIX: Fixed over-indentation — body was never executed before.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Health check first — avoid waiting if offline
            try:
                h = await client.get(f"{ACE_STEP_API}/health", timeout=3.0)
                if h.status_code != 200:
                    raise ConnectionError("offline")
            except Exception:
                return {"lyrics": "", "caption": ""}

            # Read audio with correct MIME
            suffix = audio_path.suffix.lower()
            mime_type = MIME_MAP.get(suffix, "audio/mpeg")
            with open(audio_path, "rb") as f:
                audio_data = f.read()

            files = {"audio_file": (audio_path.name, audio_data, mime_type)}

            caption = ""
            lyrics = ""

            # Style caption
            try:
                resp_desc = await client.post(f"{ACE_STEP_API}/describe", files=files)
                if resp_desc.status_code == 200:
                    caption = resp_desc.json().get("caption", "")
            except Exception:
                pass

            # Lyrics transcription
            try:
                resp_trans = await client.post(f"{ACE_STEP_API}/transcribe", files=files)
                if resp_trans.status_code == 200:
                    lyrics = resp_trans.json().get("lyrics", "")
            except Exception:
                pass

            return {"lyrics": lyrics, "caption": caption}

    except Exception as e:
        logger.warning(f"Audio analysis failed: {e}")

    return {"lyrics": "", "caption": ""}


def _run_ffmpeg(cmd: list) -> tuple:
    """Run ffmpeg synchronously — called via asyncio.to_thread (BUG 2 fix)."""
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode, result.stderr


async def _pitch_shift_only(vocal_path: Path, output_path: Path, pitch_shift: int) -> Path:
    """
    Fallback pitch shift via FFmpeg.
    BUG 2 FIX: Uses subprocess.run via asyncio.to_thread instead of
                asyncio.create_subprocess_exec (which crashes on Windows).
    """
    if pitch_shift == 0:
        shutil.copy2(vocal_path, output_path)
        logger.info(f"No pitch shift — copied vocal to {output_path.name}")
        return output_path

    # Rate-based pitch shift: asetrate moves pitch+speed, atempo corrects speed back
    semitone_ratio = 2 ** (pitch_shift / 12)
    original_rate = 44100
    shifted_rate = int(original_rate * semitone_ratio)
    tempo_correct = 1.0 / semitone_ratio

    # Clamp atempo to valid range 0.5-2.0 (FFmpeg limitation)
    # For semitones beyond ±12, chain two atempo filters
    if 0.5 <= tempo_correct <= 2.0:
        atempo_filter = f"atempo={tempo_correct:.6f}"
    elif tempo_correct < 0.5:
        # e.g. +14 semitones: ratio=2.378 → 1/2.378=0.420 → chain 0.5,0.840
        atempo_filter = f"atempo=0.5,atempo={tempo_correct/0.5:.6f}"
    else:
        # e.g. -14 semitones: ratio=0.420 → 1/0.420=2.378 → chain 2.0,1.189
        atempo_filter = f"atempo=2.0,atempo={tempo_correct/2.0:.6f}"

    af = f"asetrate={shifted_rate},aresample={original_rate},{atempo_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(vocal_path),
        "-af", af,
        "-ar", str(original_rate),
        "-q:a", "0",
        str(output_path),
    ]

    logger.info(f"FFmpeg pitch shift {pitch_shift:+d}st (ratio={semitone_ratio:.4f})")
    returncode, stderr = await asyncio.to_thread(_run_ffmpeg, cmd)

    if returncode != 0:
        raise RuntimeError(f"FFmpeg pitch shift failed: {stderr[-300:]}")

    logger.info(f"✅ Pitch shifted {pitch_shift:+d}st → {output_path.name}")
    return output_path
