"""
Instrumental Enhancer
ปรับปรุง instrumental track ด้วย ACE-Step repaint/edit
- ทำนองดีขึ้น (melody enhancement)
- จังหวะแม่นยำขึ้น (rhythm tightening)
- คุณภาพเสียงดีขึ้น (audio quality)
- รักษา structure เดิม (same song structure)

ถ้า ACE-Step offline → fallback ไปใช้ FFmpeg EQ + mastering เท่านั้น
"""

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MIME_MAP = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".m4a": "audio/mp4",
}


def _run_ffmpeg(cmd: list) -> tuple:
    result = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return result.returncode, result.stderr


async def enhance_instrumental(
    instrumental_path: Path,
    output_path: Path,
    style_prompt: str = "",
    enhance_level: float = 0.3,   # 0.0 = no change, 1.0 = full retake
    job=None,
) -> Path:
    """
    ปรับปรุง instrumental:
    1. ลอง ACE-Step audio2audio (enhance_level ต่ำ = รักษา structure)
    2. Fallback → FFmpeg EQ + dynamic enhancement
    """
    if job:
        job.update(job.status, job.progress, "ปรับปรุง instrumental (ACE-Step)...")

    # Try ACE-Step first
    ace_result = await _enhance_via_ace_step(
        instrumental_path, output_path, style_prompt, enhance_level
    )
    if ace_result:
        logger.info(f"✅ Instrumental enhanced via ACE-Step → {output_path.name}")
        return ace_result

    # Fallback: FFmpeg professional EQ + mastering
    if job:
        job.update(job.status, job.progress, "ACE-Step offline — ใช้ FFmpeg Enhancement...")
    logger.info("ACE-Step offline — falling back to FFmpeg enhancement")
    return await _enhance_via_ffmpeg(instrumental_path, output_path)


async def _enhance_via_ace_step(
    instrumental_path: Path,
    output_path: Path,
    style_prompt: str,
    enhance_level: float,
) -> Path | None:
    """
    ใช้ ACE-Step audio2audio กับ ref_audio_strength ต่ำ
    เพื่อ "remaster" instrumental โดยรักษา structure เดิม
    """
    import httpx, os
    from dotenv import load_dotenv
    load_dotenv()
    ace_url = os.getenv("ACE_STEP_API_URL", "http://100.80.64.28:8001")

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            # Health check
            try:
                h = await client.get(f"{ace_url}/health", timeout=4.0)
                if h.status_code != 200:
                    return None
            except Exception:
                return None

            # Build enhance prompt
            base_prompt = style_prompt or "professional studio quality, clean mix, punchy drums, clear instruments"
            enhance_prompt = (
                f"{base_prompt}, "
                f"improved rhythm, better timing, enhanced melody, "
                f"professional mastering, high quality audio"
            )

            suffix = instrumental_path.suffix.lower()
            mime = MIME_MAP.get(suffix, "audio/mpeg")
            audio_bytes = instrumental_path.read_bytes()

            payload = {
                "audio_duration": -1,
                "prompt": enhance_prompt,
                "lyrics": "",
                "infer_step": 15,           # น้อย step = เปลี่ยนน้อย = รักษา structure
                "guidance_scale": 4.0,
                "scheduler_type": "euler",
                "cfg_type": "apg",
                "omega_scale": 8.0,
                "actual_seeds": [42],
                "guidance_interval": 0.5,
                "guidance_interval_decay": 0.0,
                "min_guidance_scale": 2.0,
                "use_erg_tag": True,
                "use_erg_lyric": False,
                "use_erg_diffusion": True,
                "audio2audio_enable": True,
                # enhance_level ต่ำ = รักษา original มาก / สูง = เปลี่ยนมาก
                "ref_audio_strength": 1.0 - (enhance_level * 0.5),  # 0.3 → 0.85
                "task": "audio2audio",
            }

            files = {"audio_file": (instrumental_path.name, audio_bytes, mime)}
            resp = await client.post(f"{ace_url}/generate", data=payload, files=files)

            if resp.status_code != 200:
                logger.warning(f"ACE-Step enhance: {resp.status_code}")
                return None

            ct = resp.headers.get("content-type", "")
            if not any(t in ct for t in ("audio/", "application/octet-stream")):
                logger.warning(f"ACE-Step returned non-audio: {ct}")
                return None

            if len(resp.content) < 2048:
                return None

            output_path.write_bytes(resp.content)
            return output_path

    except Exception as e:
        logger.warning(f"ACE-Step enhance error: {e}")
        return None


async def _enhance_via_ffmpeg(
    instrumental_path: Path,
    output_path: Path,
) -> Path:
    """
    FFmpeg professional enhancement chain:
    - Multiband EQ: ตัด mud, boost presence
    - Dynamic compression: จังหวะชัดขึ้น
    - Stereo widening: เสียงกว้างขึ้น
    - Limiter + Loudnorm: ดัง + สะอาด
    """
    filter_chain = (
        # High-pass ตัด rumble
        "highpass=f=30,"
        # Multiband EQ
        "equalizer=f=200:width_type=o:width=2:g=-2,"    # ตัด mud
        "equalizer=f=400:width_type=o:width=2:g=-1,"    # ตัด boxiness
        "equalizer=f=3000:width_type=o:width=2:g=2,"    # boost presence
        "equalizer=f=8000:width_type=o:width=2:g=1.5,"  # boost air
        "equalizer=f=12000:width_type=o:width=2:g=1,"   # high sparkle
        # Drum transient sharpening via compression
        "acompressor=threshold=-20dB:ratio=4:attack=3:release=80:makeup=2,"
        # Stereo widening
        "stereotools=mlev=0.015,"
        # Final master
        "loudnorm=I=-12:TP=-0.5:LRA=9,"
        "alimiter=limit=0.98:level=true"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(instrumental_path),
        "-af", filter_chain,
        "-ar", "44100",
        "-c:a", "libmp3lame", "-b:a", "320k",
        str(output_path),
    ]

    returncode, stderr = await asyncio.to_thread(_run_ffmpeg, cmd)
    if returncode != 0:
        logger.error(f"FFmpeg enhance failed: {stderr[-300:]}")
        # Last resort: just copy
        import shutil
        shutil.copy2(instrumental_path, output_path)
        logger.warning("FFmpeg enhance failed — using original instrumental")

    return output_path
