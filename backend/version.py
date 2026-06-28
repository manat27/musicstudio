import json
import os
from datetime import datetime

VERSION_FILE = os.path.join(os.path.dirname(__file__), 'version.json')

def get_version():
    """อ่านเวอร์ชันปัจจุบัน"""
    if not os.path.exists(VERSION_FILE):
        # ถ้ายังไม่มีไฟล์ ให้สร้างเวอร์ชันเริ่มต้น
        save_version("1.0.0")
        return "1.0.0"
    
    with open(VERSION_FILE, 'r') as f:
        data = json.load(f)
        return data.get('version', '1.0.0')

def save_version(version_str):
    """บันทึกเวอร์ชันใหม่พร้อม timestamp"""
    data = {
        'version': version_str,
        'updated_at': datetime.now().isoformat(),
        'build': datetime.now().strftime('%Y%m%d%H%M%S')
    }
    with open(VERSION_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    return data

def bump_version(part='patch'):
    """
    เพิ่มเวอร์ชันอัตโนมัติ
    part: 'major', 'minor', หรือ 'patch'
    """
    current = get_version()
    parts = list(map(int, current.split('.')))
    
    if part == 'major':
        parts[0] += 1
        parts[1] = 0
        parts[2] = 0
    elif part == 'minor':
        parts[1] += 1
        parts[2] = 0
    elif part == 'patch':
        parts[2] += 1
    
    new_version = ".".join(map(str, parts))
    save_version(new_version)
    return new_version

if __name__ == "__main__":
    print(f"Current Version: {get_version()}")
