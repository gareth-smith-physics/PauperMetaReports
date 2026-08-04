from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports.discord_sync import run_sync

if __name__ == "__main__":
    run_sync()
