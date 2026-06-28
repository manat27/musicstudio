"""
AI Cover Studio - Backend API Phase 2 + Suno Features
"""

import sys
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from api.routes import router as cover_router
from api.suno_routes import router as suno_router
from core.job_manager import job_manager
from version import get_version

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ดึงเวอร์ชันปัจจุบัน
CURRENT_VERSION = get_version()

for d in ["uploads", "outputs", "outputs/tasks"]:
    Path(d).mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🎵 AI Cover Studio starting...")
    await job_manager.start()
    yield
    await job_manager.stop()


app = FastAPI(title="AI Cover Studio API", version=CURRENT_VERSION, lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

app.include_router(cover_router, prefix="/api")
app.include_router(suno_router, prefix="/api/suno")

app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")

frontend_path = Path("frontend/dist")
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AI Cover Studio", "version": CURRENT_VERSION}


@app.get("/api/version")
async def get_version_info():
    """ข้อมูลเวอร์ชันปัจจุบัน"""
    from version import get_version, save_version
    current = get_version()
    return {
        "version": current,
        "service": "AI Cover Studio",
        "build_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


@app.post("/api/version/bump")
async def bump_version(part: str = "patch"):
    """เพิ่มเวอร์ชันอัตโนมัติ (major, minor, patch)"""
    from version import bump_version as bv
    try:
        new_ver = bv(part)
        return {"success": True, "new_version": new_ver, "message": f"Version bumped to {new_ver}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8081, reload=False, log_level="info")
