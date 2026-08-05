from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import get_collection

OUTPUT_PATH = ROOT / "data" / "raw_lines.txt"


def main() -> None:
    history_coll = get_collection("history")

    seen: set[str] = set()
    lines: list[str] = []
    for doc in history_coll.find({}):
        for result in doc.get("results", []):
            raw_line = (result.get("raw_line") or "").strip()
            if raw_line and raw_line not in seen:
                seen.add(raw_line)
                lines.append(raw_line)

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines) + "\n")
    print(f"Wrote {len(lines)} raw line(s) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
