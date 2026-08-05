from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports.discord_sync import run_sync

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Re-scan the whole channel from START_DATE, instead of resuming from the last "
            "recorded report - picks up messages that were previously skipped, failed to "
            "produce a report at all, or only PARTLY parsed (some lines missing from an "
            "otherwise-existing report) under older parsing logic. Safe to run repeatedly: "
            "it only ever adds a new report or fills in a missing line (matched by its exact "
            "raw text), never touches or duplicates a result that's already recorded. It will "
            "NOT fix a result that's already there but was parsed *incorrectly* under old "
            "logic - that needs a targeted correction instead (e.g. scripts/edit_result.py "
            "or scripts/delete_report.py + a re-run)."
        )
    )
    parser.add_argument(
        "--default-lgs",
        help=(
            "Fallback LGS name for messages that don't mention a known store. "
            "Needed for the very first run, before any LGS has been registered."
        ),
    )
    args = parser.parse_args()
    run_sync(default_lgs=args.default_lgs, full_rescan=True)
