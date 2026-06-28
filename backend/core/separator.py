"""
Stem Separation - Demucs integration
Windows-compatible: uses asyncio.to_thread + subprocess.run (NO asyncio.create_subprocess_exec)
"""

import asyncio
import logging
import subprocess
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs_ft")


def _run_demucs(cmd: list, log_callback=None) -> tuple:
    """Run demucs synchronously in a thread (Windows-safe) with real-time log tracking."""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    full_output = []
    for line in process.stdout:
        line = line.strip()
        if line:
            full_output.append(line)
            if log_callback:
                # Capture progress patterns from Demucs output (e.g., "75.0%")
                import re
                progress_match = re.search(r"(\d+\.\d+)%", line)
                if progress_match:
                    pct = float(progress_match.group(1))
                    log_callback(pct, line)
                elif any(word in line.lower() for word in ["separated", "saving", "stems"]):
                    log_callback(None, line)

    process.wait()
    return process.returncode, "\n".join(full_output), ""


async def separate_stems(input_path: Path, output_dir: Path, gpu_id: int = 0, job=None) -> dict:
    """
    Run Demucs stem separation using a temporary workspace to avoid Windows Path issues.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    def update_progress(pct, line):
        if job:
            current_pct = 5 + int((pct / 100) * 30) if pct is not None else job.progress
            job.update(job.status, current_pct, f"แยกเสียง (Demucs): {line}")

    # Use a simple temporary directory without spaces
    temp_workspace = Path(tempfile.gettempdir()) / "ai_cover_work"
    temp_workspace.mkdir(exist_ok=True)
    
    # Create a simple safe name for the input file
    safe_input_name = f"input_{uuid.uuid4().hex[:8]}{input_path.suffix}"
    safe_input_path = temp_workspace / safe_input_name
    
    # Copy file to safe location
    logger.info(f"🚚 Copying input to safe workspace: {safe_input_path}")
    shutil.copy2(input_path, safe_input_path)

    import sys
    python_exe = sys.executable

    default_gpu = int(os.getenv("DEFAULT_GPU_ID", "0"))
    gpu_id = gpu_id if gpu_id is not None else default_gpu
    device = "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            if gpu_id < torch.cuda.device_count():
                device = f"cuda:{gpu_id}"
            else:
                device = "cuda:0"
            torch.backends.cudnn.enabled = True
            torch.backends.cudnn.benchmark = True
            logger.info(f"🚀 CUDA detected: {torch.cuda.get_device_name(device)}! Using {device}.")
    except ImportError:
        pass

    def build_cmd(dev, inp, out):
        # The most basic command possible
        return [
            python_exe, "-m", "demucs.separate",
            "--name", str(DEMUCS_MODEL),
            "--out", str(out),
            "--mp3",
            "--two-stems", "vocals",
            "--device", str(dev),
            str(inp)
        ]

    # Temporary output for demucs
    temp_out = temp_workspace / f"out_{uuid.uuid4().hex[:8]}"
    temp_out.mkdir(exist_ok=True)

    cmd = build_cmd(device, safe_input_path, temp_out)
    logger.info(f"Running Demucs ({device}) in safe workspace")
    
    # Custom run for thread
    def _run_demucs_direct(cmd_list, log_callback=None):
        process = subprocess.Popen(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        full_output = []
        for line in process.stdout:
            line = line.strip()
            if line:
                full_output.append(line)
                if log_callback:
                    import re
                    progress_match = re.search(r"(\d+\.\d+)%", line)
                    if progress_match:
                        pct = float(progress_match.group(1))
                        log_callback(pct, line)
                    elif any(word in line.lower() for word in ["separated", "saving", "stems"]):
                        log_callback(None, line)
        process.wait()
        return process.returncode, "\n".join(full_output), ""

    returncode, stdout, stderr = await asyncio.to_thread(_run_demucs_direct, cmd, update_progress)

    # Fallback to CPU
    if returncode != 0 and device != "cpu":
        logger.warning("GPU failed, trying CPU...")
        device = "cpu"
        cmd = build_cmd(device, safe_input_path, temp_out)
        returncode, stdout, stderr = await asyncio.to_thread(_run_demucs_direct, cmd, update_progress)

    if returncode != 0:
        # Cleanup
        if safe_input_path.exists(): safe_input_path.unlink()
        raise RuntimeError(f"Demucs separation failed: {stdout[-500:]}")

    # Move results back to original output_dir
    # Demucs structure: temp_out / model_name / input_name / vocals.mp3
    model_folder = DEMUCS_MODEL
    input_folder = safe_input_path.stem
    result_vocals = temp_out / model_folder / input_folder / "vocals.mp3"
    result_no_vocals = temp_out / model_folder / input_folder / "no_vocals.mp3"

    final_vocals = output_dir / "vocals.mp3"
    final_no_vocals = output_dir / "instrumental.mp3"

    if result_vocals.exists():
        shutil.move(str(result_vocals), str(final_vocals))
    if result_no_vocals.exists():
        shutil.move(str(result_no_vocals), str(final_no_vocals))

    # Cleanup temp workspace for this job
    try:
        shutil.rmtree(temp_out)
        if safe_input_path.exists(): safe_input_path.unlink()
    except:
        pass

    return {
        "vocals": final_vocals,
        "instrumental": final_no_vocals
    }
