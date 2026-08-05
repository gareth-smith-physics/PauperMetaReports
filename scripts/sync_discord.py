from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports.discord_sync import run_sync

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Discord meta-report sync.")
    parser.add_argument(
        "--default-lgs",
        help=(
            "Fallback LGS name for messages that don't mention a known store. "
            "Needed for the very first run, before any LGS has been registered."
        ),
    )
    args = parser.parse_args()
    run_sync(default_lgs=args.default_lgs)
