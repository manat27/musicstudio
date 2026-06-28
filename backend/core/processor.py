"""
Cover Processor - New Pipeline v2
────────────────────────────────────────────────────────────
Pipeline ใหม่:
  1. Demucs       → vocal.mp3 + instrumental.mp3
  2. (parallel)
     2a. Voice Conversion  → vocal_new.mp3   (เสียงร้องใหม่ เนื้อร้องเดิม)
     2b. Instrumental Enhance → inst_enhanced.mp3 (ทำนอง+จังหวะดีขึ้น)
  3. Auto Mix     → cover_final.mp3 + cover_final.wav
  4. Export stems

Mode ที่รองรับ:
  vc    = voice cover เท่านั้น (เร็ว)
  full  = voice cover + instrumental enhancement (ค่า default ใหม่)
  suno  = full re-imagine via ACE-Step (สร้างใหม่ทั้งหมด)
"""

import asyncio
import logging
import shutil
from pathlib import Path

from core.job_manager import Job, JobStatus, job_manager
from core.separator import separate_stems
from core.voice_converter import convert_voice, analyze_audio
from core.instrumental_enhancer import enhance_instrumental
from core.mixer import auto_mix, export_stems

logger = logging.getLogger(__name__)

OUTPUT_BASE = Path("outputs")


async def process_cover_job(job: Job):
    job_dir = OUTPUT_BASE / job.id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        input_path = Path(job.input_file)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        mode = getattr(job, "mode", "full") or "full"

        # ═══════════════════════════════════════════════════════════
        # Stage 1 — Demucs: แยก vocal + instrumental
        # ═══════════════════════════════════════════════════════════
        job.update(JobStatus.SEPARATING, 5, "🎵 แยกเสียง (Demucs htdemucs_ft)...")

        stems = await separate_stems(
            input_path,
            job_dir / "stems",
            gpu_id=getattr(job, "gpu_id", 0),
            job=job,
        )
        vocal_path = stems["vocals"]
        instrumental_path = stems["instrumental"]

        job.update(JobStatus.SEPARATING, 32, "✓ แยกเสียงสำเร็จ")

        # ═══════════════════════════════════════════════════════════
        # Stage 1.5 — Analyze: ดึงเนื้อร้องและสไตล์ (สำหรับ remix/cover)
        # ═══════════════════════════════════════════════════════════
        job.update(JobStatus.SEPARATING, 35, "🔍 วิเคราะห์เนื้อร้องและสไตล์...")
        analysis = await analyze_audio(vocal_path)
        job.lyrics = analysis.get("lyrics", "")
        job.music_caption = analysis.get("caption", "")
        job_manager.save_job(job)
        logger.info(f"[Job {job.id[:8]}] Lyrics detected: {len(job.lyrics)} chars")

        # ═══════════════════════════════════════════════════════════
        # Stage 2 — Parallel: Voice Convert + Instrumental Enhance
        # ═══════════════════════════════════════════════════════════
        job.update(JobStatus.CONVERTING, 38, "🎤 แปลงเสียงร้อง + ปรับปรุง instrumental...")

        converted_vocal = job_dir / "vocal_new.mp3"
        inst_enhanced   = job_dir / "instrumental_enhanced.mp3"

        # Run BOTH in parallel to save time
        if mode == "vc":
            # Quick mode: voice change only, keep instrumental as-is
            voice_task = convert_voice(
                vocal_path=vocal_path,
                output_path=converted_vocal,
                voice_model=job.voice_model,
                pitch_shift=job.pitch,
                style_prompt=job.style_prompt,
                lyrics=job.lyrics,  # ใช้เนื้อร้องที่ดึงมา
            )
            # Copy instrumental without enhancement
            async def _copy_inst():
                shutil.copy2(instrumental_path, inst_enhanced)
                return inst_enhanced

            await asyncio.gather(voice_task, _copy_inst())

        elif mode == "suno":
            # Full suno mode: re-imagine both vocal and instrumental
            voice_task = convert_voice(
                vocal_path=vocal_path,
                output_path=converted_vocal,
                voice_model="suno_mode",
                pitch_shift=job.pitch,
                style_prompt=job.style_prompt,
                lyrics=job.lyrics,  # ใช้เนื้อร้องที่ดึงมา
            )
            enhance_task = enhance_instrumental(
                instrumental_path=instrumental_path,
                output_path=inst_enhanced,
                style_prompt=job.style_prompt,
                enhance_level=0.6,   # เปลี่ยนได้มากกว่า
                job=None,
            )
            await asyncio.gather(voice_task, enhance_task)

        else:
            # "full" mode (default): voice change + instrumental enhance
            # ─── 2a: Voice Conversion ────────────────────────────
            # ─── 2b: Instrumental Enhancement ────────────────────
            # Run in parallel
            voice_task = convert_voice(
                vocal_path=vocal_path,
                output_path=converted_vocal,
                voice_model=job.voice_model,
                pitch_shift=job.pitch,
                style_prompt=job.style_prompt,
                lyrics=job.lyrics,  # ใช้เนื้อร้องที่ดึงมา
            )
            enhance_task = enhance_instrumental(
                instrumental_path=instrumental_path,
                output_path=inst_enhanced,
                style_prompt=job.style_prompt,
                enhance_level=getattr(job, "enhance_level", 0.3),
                job=None,
            )
            await asyncio.gather(voice_task, enhance_task)

        job.update(JobStatus.CONVERTING, 70, "✓ เสียงร้องใหม่ + instrumental ปรับปรุงแล้ว")

        # ═══════════════════════════════════════════════════════════
        # Stage 3 — Auto Mix
        # เสียงร้องใหม่ + instrumental ที่ปรับปรุงแล้ว
        # ═══════════════════════════════════════════════════════════
        job.update(JobStatus.MIXING, 73, "🎚️ Auto Mix + EQ + Mastering...")

        cover_mp3 = job_dir / "cover.mp3"
        cover_wav = job_dir / "cover.wav"

        mix_blend = getattr(job, "mix_blend", 0.8)
        if mode == "suno":
            mix_blend = 0.85

        # Mix MP3 + WAV in parallel
        await asyncio.gather(
            auto_mix(
                vocal_path=converted_vocal,
                instrumental_path=inst_enhanced,
                output_path=cover_mp3,
                reverb=getattr(job, "reverb", 0.2),
                mix_blend=mix_blend,
                output_format="mp3",
            ),
            auto_mix(
                vocal_path=converted_vocal,
                instrumental_path=inst_enhanced,
                output_path=cover_wav,
                reverb=getattr(job, "reverb", 0.2),
                mix_blend=mix_blend,
                output_format="wav",
            ),
        )

        job.update(JobStatus.MIXING, 88, "✓ Mixing สำเร็จ")

        # ═══════════════════════════════════════════════════════════
        # Stage 4 — Export stems
        # ═══════════════════════════════════════════════════════════
        job.update(JobStatus.EXPORTING, 90, "📦 Export stems...")

        await export_stems(converted_vocal, inst_enhanced, job_dir)

        # ═══════════════════════════════════════════════════════════
        # Done
        # ═══════════════════════════════════════════════════════════
        job.outputs = {
            "cover_mp3":         f"/api/cover/download/{job.id}/cover.mp3",
            "cover_wav":         f"/api/cover/download/{job.id}/cover.wav",
            "vocal_wav":         f"/api/cover/download/{job.id}/vocal.wav",
            "instrumental_wav":  f"/api/cover/download/{job.id}/instrumental.wav",
        }

        job.update(JobStatus.DONE, 100, "🎵 เสร็จสิ้น!")
        logger.info(f"[Job {job.id[:8]}] ✅ {mode.upper()} cover done → {cover_mp3.name}")

    except Exception as e:
        job.update(JobStatus.ERROR, job.progress, f"❌ {str(e)}")
        job.error = str(e)
        job_manager.save_job(job)
        logger.error(f"[Job {job.id[:8]}] Failed: {e}", exc_info=True)
        raise


job_manager.set_processor(process_cover_job)
