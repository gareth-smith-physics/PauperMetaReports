from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import (
    DeckRegistry,
    History,
    LGSRegistry,
    NameRegistry,
    ask_queue_for_review,
    find_lgs_in_message,
    is_meta_report,
    parse_meta_report,
    parse_new_lgs_announcement,
    queue_lgs_review,
)

DEFAULT_LGS = "Unknown LGS"


def main() -> None:
    print("Paste the meta report message below, then press Ctrl+D to finish:\n")
    message = sys.stdin.read()

    if not message.strip():
        print("Nothing pasted - aborting.")
        return

    date_input = input(f"\nReport date [{date.today().isoformat()}]: ").strip()
    report_date = date.fromisoformat(date_input) if date_input else date.today()

    name_registry = NameRegistry()
    deck_registry = DeckRegistry()
    lgs_registry = LGSRegistry()
    history = History.load()

    # Mirrors discord_sync.py's per-message logic exactly, so this behaves
    # like the real headless bot would have processed the same text: an
    # LGS announcement gets registered, ambiguous names/decks (and a missing
    # venue) get queued for review instead of guessed, nothing is asked
    # interactively.
    new_lgs = parse_new_lgs_announcement(message)
    if new_lgs is not None:
        if lgs_registry.add_canonical(new_lgs):
            print(f"Registered new LGS: {new_lgs}")

    if not is_meta_report(message, name_registry, deck_registry):
        print("This doesn't look like a meta report - nothing added.")
        return

    found_event = find_lgs_in_message(message, lgs_registry)
    event = found_event or DEFAULT_LGS
    if found_event is None:
        queue_lgs_review(report_date, DEFAULT_LGS, message)
        print(f"No known LGS mentioned - recorded under '{DEFAULT_LGS}' and queued for review.")

    if history.has_report(report_date, event):
        print(f"A report for {report_date} @ {event} is already in the database - nothing added.")
        return

    report = parse_meta_report(
        message,
        date=report_date,
        event=event,
        name_registry=name_registry,
        deck_registry=deck_registry,
        name_ask=ask_queue_for_review("names", report_date, event),
        deck_ask=ask_queue_for_review("decks", report_date, event),
    )
    history.add(report)

    print(f"\nAdded report: {report_date} @ {event} - {len(report)} result(s).")
    for result in report:
        player_display = result.player or "(unresolved - queued for review)"
        deck_display = result.deck or "(unresolved - queued for review)"
        print(f"  {player_display:20} | {deck_display:25} | {result.record}")


if __name__ == "__main__":
    main()
