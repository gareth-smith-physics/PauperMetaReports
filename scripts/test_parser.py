from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import DeckRegistry, NameRegistry
from pauper_meta_reports.parser import parse_result_line

INPUT_PATH = ROOT / "data" / "raw_lines.txt"

_DUMMY_DATE = date.today()
_DUMMY_EVENT = "test"


def _skip(raw: str, candidate: str | None, score: float, matched_alias: str | None) -> str:
    """Never accept/create anything - this is just eyeballing the split
    logic against real registry content, not real ingestion."""
    return ""


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"{INPUT_PATH} not found - run scripts/extract_raw_lines.py first.")
        return

    lines = [line.strip() for line in INPUT_PATH.read_text().splitlines() if line.strip()]
    if not lines:
        print(f"{INPUT_PATH} is empty - nothing to test.")
        return

    name_registry = NameRegistry()
    deck_registry = DeckRegistry()

    failures = 0
    for line in lines:
        result = parse_result_line(
            line,
            date=_DUMMY_DATE,
            event=_DUMMY_EVENT,
            name_registry=name_registry,
            deck_registry=deck_registry,
            name_ask=_skip,
            deck_ask=_skip,
            swapped=False,
        )
        print(f"RAW:    {line}")
        if result is None:
            print("PARSED: (no parse - no record match, or name/deck came out empty)")
            failures += 1
        else:
            print(f"PARSED: player={result.raw_player!r}  deck={result.raw_deck!r}  record={result.record}")
        print()

    print(f"{len(lines)} line(s), {failures} failed to parse at all.")


if __name__ == "__main__":
    main()
