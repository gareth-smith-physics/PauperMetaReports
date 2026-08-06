from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import (
    DeckRegistry,
    History,
    LGSRegistry,
    MetaReport,
    NameRegistry,
    Record,
    Result,
    ask_queue_for_review,
)

# CSV dates are Month/Day/Year (confirmed with the user - "7/6/2026" = July 6, 2026).
DATE_FORMAT = "%m/%d/%Y"


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/import_csv.py path/to/file.csv")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("CSV has no rows - nothing to add.")
        return

    name_registry = NameRegistry()
    deck_registry = DeckRegistry()
    lgs_registry = LGSRegistry()
    history = History.load()

    # Group rows into one MetaReport per (date, venue) pair, same unit the
    # rest of the app treats as a single meta report.
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        report_date = datetime.strptime(row["Date"].strip(), DATE_FORMAT).date()
        grouped[(report_date, row["Location"].strip())].append(row)

    added_reports = 0
    skipped_reports = 0

    for (report_date, raw_location), group_rows in grouped.items():
        # Explicit, human-typed venue name - no ambiguous message text to
        # parse out of, so resolve it directly instead of queuing for review.
        event = lgs_registry.resolve(raw_location)

        if history.has_report(report_date, event):
            print(f"Skipped {report_date} @ {event}: already in the database.")
            skipped_reports += 1
            continue

        report = MetaReport(date=report_date, event=event)
        for row in group_rows:
            raw_player = row["Name"].strip()
            raw_deck = row["Deck"].strip()
            player = name_registry.resolve(
                raw_player, ask=ask_queue_for_review("names", report_date, event)
            )
            deck = deck_registry.resolve(
                raw_deck, ask=ask_queue_for_review("decks", report_date, event)
            )
            record = Record(
                wins=int(row["Win"]),
                losses=int(row["Loss"]),
                draws=int(row["Draw"] or 0),
            )
            report.add(
                Result(
                    player=player,
                    deck=deck,
                    record=record,
                    date=report_date,
                    event=event,
                    raw_player=raw_player,
                    raw_deck=raw_deck,
                    raw_line=f"{raw_player},{raw_deck},{record}",
                )
            )

        history.add(report)
        added_reports += 1
        print(f"Added {report_date} @ {event}: {len(report)} result(s).")
        for result in report:
            player_display = result.player or "(unresolved - queued for review)"
            deck_display = result.deck or "(unresolved - queued for review)"
            print(f"  {player_display:20} | {deck_display:25} | {result.record}")

    print(f"\nDone. {added_reports} report(s) added, {skipped_reports} already present.")


if __name__ == "__main__":
    main()
