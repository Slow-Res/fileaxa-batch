"""Regenerate src/fileaxa_batch/data/queue_template.db from the schema in
queue_store.SCHEMA. Run this any time the schema changes; commit the result.

    python scripts/build_template_db.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fileaxa_batch.core.queue_store import SCHEMA  # noqa: E402

TARGET = ROOT / "src" / "fileaxa_batch" / "data" / "queue_template.db"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        TARGET.unlink()
    cx = sqlite3.connect(TARGET)
    try:
        cx.executescript(SCHEMA)
        cx.commit()
    finally:
        cx.close()
    print(f"wrote {TARGET} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
