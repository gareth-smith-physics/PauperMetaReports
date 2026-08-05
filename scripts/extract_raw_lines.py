from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import get_collection

OUTPUT_PATH = ROOT / "data" / "raw_lines.txt"


def main() -> None:
    history_coll = get_collection("history")

    reports: list[list[str]] = []
    for doc in history_coll.find({}).sort([("date", 1), ("event", 1)]):
        lines = [
            raw_line
            for result in doc.get("results", [])
            if (raw_line := (result.get("raw_line") or "").strip())
        ]
        if lines:
            reports.append(lines)

    # Grouped by report and kept in on-the-page order (no dedup, unlike
    # before) - test_parser.py needs each report's lines intact and
    # together to vote on field order the same way the real pipeline does,
    # not a flattened, deduplicated sample of just the distinct raw text.
    # "..." on its own line separates reports, same convention
    # discord_messages.txt/demo.py already use for distinct messages.
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    blocks = ["\n".join(lines) for lines in reports]
    OUTPUT_PATH.write_text("\n...\n".join(blocks) + "\n")

    total_lines = sum(len(lines) for lines in reports)
    print(f"Wrote {len(reports)} report(s), {total_lines} raw line(s) total, to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
