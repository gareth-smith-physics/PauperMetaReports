from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import DeckRegistry, NameRegistry
from pauper_meta_reports.parser import _report_delimiters, _report_order_is_swapped, parse_result_line

INPUT_PATH = ROOT / "data" / "raw_lines.txt"

_DUMMY_DATE = date.today()
_DUMMY_EVENT = "test"


def _skip(raw: str, candidate: str | None, score: float, matched_alias: str | None) -> str:
    """Never accept/create anything - this is just eyeballing the split
    logic against real registry content, not real ingestion."""
    return ""


def _split_into_reports(text: str) -> list[str]:
    """raw_lines.txt groups lines by their original report, separated by a
    lone '...' line - same convention discord_messages.txt/demo.py already
    use for distinct messages (see extract_raw_lines.py)."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "...":
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [b for b in blocks if b.strip()]


def main() -> None:
    if not INPUT_PATH.exists():
        print(f"{INPUT_PATH} not found - run scripts/extract_raw_lines.py first.")
        return

    reports = _split_into_reports(INPUT_PATH.read_text())
    if not reports:
        print(f"{INPUT_PATH} is empty - nothing to test.")
        return

    name_registry = NameRegistry()
    deck_registry = DeckRegistry()

    total_lines = 0
    failures = 0
    for report_text in reports:
        # The same report-wide inference the real pipeline uses
        # (parse_meta_report computes both once per message): which
        # delimiter characters this report actually uses, and which end of
        # the line the player is on - a line with no evidence of its own
        # (still-unregistered name and deck, or an incidental character
        # that isn't really this report's delimiter) borrows the verdict
        # from its siblings in the same report, instead of being judged
        # alone.
        delimiters = _report_delimiters(report_text)
        swapped = _report_order_is_swapped(report_text, delimiters, name_registry, deck_registry)
        for line in report_text.splitlines():
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            result = parse_result_line(
                line,
                date=_DUMMY_DATE,
                event=_DUMMY_EVENT,
                name_registry=name_registry,
                deck_registry=deck_registry,
                name_ask=_skip,
                deck_ask=_skip,
                delimiters=delimiters,
                swapped=swapped,
            )
            print(f"RAW:    {line}")
            if result is None:
                print("PARSED: (no parse - no record match, or name/deck came out empty)")
                failures += 1
            else:
                print(f"PARSED: player={result.raw_player!r}  deck={result.raw_deck!r}  record={result.record}")
            print()

    print(f"{len(reports)} report(s), {total_lines} line(s), {failures} failed to parse at all.")


if __name__ == "__main__":
    main()
