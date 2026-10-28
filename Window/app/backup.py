"""存档：备份 / 恢复桌面端的配置与校准。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
WINDOW_DIR = APP_DIR.parent
REPO = WINDOW_DIR.parents[1]
BACKUP_DIR = WINDOW_DIR / "backups"

TARGETS = [
    WINDOW_DIR / "config.json",
    REPO / "calibration.json",
]


def create_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / stamp
    target.mkdir(parents=True, exist_ok=True)
    saved = []
    for source in TARGETS:
        if source.is_file():
            shutil.copy2(source, target / source.name)
            saved.append(source.name)
    (target / "info.json").write_text(
        json.dumps({"time": stamp, "files": saved}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def list_backups() -> list[Path]:
    if not BACKUP_DIR.is_dir():
        return []
    return sorted((p for p in BACKUP_DIR.iterdir() if p.is_dir()), reverse=True)


def restore_backup(folder: str | Path) -> list[str]:
    source = Path(folder)
    restored = []
    for name in ("config.json", "calibration.json"):
        candidate = source / name
        if not candidate.is_file():
            continue
        for target in TARGETS:
            if target.name == name:
                shutil.copy2(candidate, target)
                restored.append(name)
    return restored
