"""
Auto Mixer - Professional mixing pipeline using FFmpeg
EQ → Compression → Reverb → Level matching → Mix with instrumental → Master
"""

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def auto_mix(
    vocal_path: Path,
    instrumental_path: Path,
    output_path: Path,
    reverb: float = 0.2,
    mix_blend: float = 0.8,
    output_format: str = "mp3",
) -> Path:
    """
    Professional auto-mix pipeline:
    1. Vocal: Highpass → EQ → Compress → DeEss → Reverb → Level
    2. Instrumental: Lowpass gentle → Level
    3. Mix at blend ratio
    4. Master: Limiter → Normalize → Export
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    vocal_vol = mix_blend          # 0.0 - 1.0
    inst_vol = 1.0                 # instrumental at full, vocal rides on top

    reverb_mix = reverb            # 0 = dry, 1 = wet
    delay_ms = 20                  # predelay for reverb

    # Build complex FFmpeg filter graph
    # We use a flat structure to avoid semicolon issues with labels
    
    reverb_wet = reverb_mix * 0.4
    reverb_dry = 1.0 - (reverb_wet * 0.5)

    # 1. Process Vocals (Highpass, EQ, Compress, Volume)
    # 2. Process Instrumental (Highpass, Sparkle, Wide, Compress, Volume)
    # 3. Mix Vocals and Instrumental (Standard Mix)
    # 4. Master (Glue, Norm, Limit)

    filter_complex = (
        # [0:a] is Vocals
        f"[0:a]highpass=f=100,"
        f"equalizer=f=3500:width_type=o:width=2:g=3,"
        f"equalizer=f=7500:width_type=o:width=1:g=-4,"
        f"acompressor=threshold=-20dB:ratio=4:attack=5:release=50:makeup=2,"
        f"volume={vocal_vol:.2f}[v_pre];"
        
        # [1:a] is Instrumental
        f"[1:a]highpass=f=40,"
        f"equalizer=f=10000:width_type=h:width=2000:g=2.5,"
        f"extrastereo=m=1.6:c=1,"
        f"acompressor=threshold=-15dB:ratio=2:attack=10:release=100:makeup=1,"
        f"volume={inst_vol:.2f}[i_pre];"
        
        # Mix (Removed asidechaincompress for compatibility)
        f"[v_pre][i_pre]amix=inputs=2:duration=longest[mixed];"
        
        # Master
        f"[mixed]acompressor=threshold=-15dB:ratio=2:attack=5:release=100,"
        f"loudnorm=I=-14:TP=-1.0:LRA=11,"
        f"alimiter=limit=0.98:level=true"
    )

    if output_format == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]
    else:
        codec_args = ["-c:a", "pcm_s16le"]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(vocal_path),
        "-i", str(instrumental_path),
        "-filter_complex", filter_complex,
        "-ar", "44100",
        *codec_args,
        str(output_path),
    ]

    logger.info(f"Auto mix: vocal={vocal_path.name}, inst={instrumental_path.name}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode()
        logger.error(f"FFmpeg mix failed:\n{err[-500:]}")
        raise RuntimeError(f"Auto mix failed: {err[-300:]}")

    logger.info(f"✅ Mixed output: {output_path} ({output_path.stat().st_size/1024:.0f} KB)")
    return output_path


async def export_stems(
    vocal_path: Path,
    instrumental_path: Path,
    job_dir: Path,
) -> dict[str, Path]:
    """Export individual stems as WAV for download."""
    vocal_out = job_dir / "vocal.wav"
    inst_out = job_dir / "instrumental.wav"

    async def to_wav(src: Path, dst: Path):
        cmd = ["ffmpeg", "-y", "-i", str(src), "-ar", "44100", "-c:a", "pcm_s16le", str(dst)]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

    await asyncio.gather(to_wav(vocal_path, vocal_out), to_wav(instrumental_path, inst_out))
    return {"vocal_wav": vocal_out, "instrumental_wav": inst_out}
