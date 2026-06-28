"""
Voice Manager - Handles dynamic voice model presets using SQLite
"""

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("jobs.db")

DEFAULT_VOICES = {
    "male_thai": {
        "tags": "thai male vocal, clear studio recording, pop, professional quality, deep voice",
        "description": "นักร้องชายไทย",
        "icon": "👨",
    },
    "female_thai": {
        "tags": "thai female vocal, high quality, sweet voice, pop, clear articulation, high pitch",
        "description": "นักร้องหญิงไทย",
        "icon": "👩",
    },
    "luktung": {
        "tags": "thai country music, luktung style, emotional male vocal, traditional vibrato",
        "description": "ลูกทุ่ง",
        "icon": "🪗",
    },
    "morlam": {
        "tags": "thai morlam, northeast thailand style, lao ethnic vocal, rhythmic",
        "description": "หมอลำ",
        "icon": "🥁",
    },
    "pop": {
        "tags": "contemporary thai pop, smooth vocal, radio ready, modern production",
        "description": "ป๊อป",
        "icon": "⭐",
    },
    "rock": {
        "tags": "thai rock style, powerful male vocal, energetic, slight grit",
        "description": "Rock",
        "icon": "🎸",
    },
    "anime": {
        "tags": "japanese anime style vocal, high energy, female, cute but powerful",
        "description": "อนิเมะ",
        "icon": "🏮",
    },
    "rapper": {
        "tags": "thai hip hop, rap vocal, clear flow, energetic",
        "description": "แร็ปเปอร์",
        "icon": "🎤",
    },
}

class VoiceManager:
    def __init__(self):
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_models (
                    id TEXT PRIMARY KEY,
                    tags TEXT,
                    description TEXT,
                    icon TEXT,
                    is_custom INTEGER DEFAULT 0
                )
            """)
            
            # Always ensure defaults are correct/updated
            for vid, v in DEFAULT_VOICES.items():
                conn.execute(
                    "INSERT OR REPLACE INTO voice_models (id, tags, description, icon, is_custom) VALUES (?, ?, ?, ?, 0)",
                    (vid, v["tags"], v["description"], v["icon"])
                )
            conn.commit()

    def list_voices(self) -> List[Dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM voice_models")
            return [dict(row) for row in cursor.fetchall()]

    def get_voice(self, voice_id: str) -> Optional[Dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM voice_models WHERE id = ?", (voice_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_voice(self, voice_id: str, tags: str, description: str, icon: str = "🎤"):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO voice_models (id, tags, description, icon, is_custom) VALUES (?, ?, ?, ?, 1)",
                (voice_id, tags, description, icon)
            )
            conn.commit()

    def delete_voice(self, voice_id: str):
        with sqlite3.connect(DB_PATH) as conn:
            # Only allow deleting custom ones? Or all? Let's allow all for now.
            conn.execute("DELETE FROM voice_models WHERE id = ?", (voice_id,))
            conn.commit()

voice_manager = VoiceManager()
