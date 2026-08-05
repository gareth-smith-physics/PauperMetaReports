from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import (
    DeckRegistry,
    History,
    NameRegistry,
    ask_terminal,
    is_meta_report,
    parse_meta_report,
    update_deck_registry_from_goldfish,
)
from pauper_meta_reports.db import get_collection

MESSAGES_FILE = ROOT / "discord_messages.txt"


def split_into_messages(text: str) -> list[str]:
    """discord_messages.txt uses lone '...' lines to separate distinct sample messages."""
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
    # Separate "_demo" collections so running this against the sample messages
    # never touches real league data sitting in the same MongoDB database.
    name_registry = NameRegistry(get_collection("names_demo"))
    deck_registry = DeckRegistry(get_collection("decks_demo"))

    history = History.load(get_collection("history_demo"))
    if history:
        print(f"Loaded history: {len(history)} report(s), {history.first_date} to {history.last_date}")
    else:
        print("Loaded history: empty")

    print("Syncing deck list from MTGGoldfish...")
    try:
        added = update_deck_registry_from_goldfish(deck_registry)
    except Exception as exc:  # network/site errors shouldn't block parsing
        print(f"  Skipped: {exc}")
    else:
        print(f"  Added {len(added)} new deck(s)." if added else "  Deck list already up to date.")

    name_ask = ask_terminal("player")
    deck_ask = ask_terminal("deck")

    messages = split_into_messages(MESSAGES_FILE.read_text())

    # Dummy per-message metadata, standing in for Discord message timestamp/thread.
    # Deterministic across runs (same messages -> same dates), so already-seen
    # reports line up with history and get skipped rather than reprocessed.
    dummy_date = date(2026, 1, 5)
    event = "Dummy LGS"

    for i, message in enumerate(messages):
        if not is_meta_report(message, name_registry, deck_registry):
            print(f"--- message {i}: not a meta report, skipping ---")
            continue

        if history.has_report(dummy_date, event):
            print(f"--- message {i}: already in history ({dummy_date} @ {event}), skipping ---")
            dummy_date += timedelta(weeks=1)
            continue

        report = parse_meta_report(
            message,
            date=dummy_date,
            event=event,
            name_registry=name_registry,
            deck_registry=deck_registry,
            name_ask=name_ask,
            deck_ask=deck_ask,
        )
        history.add(report)  # persists to MongoDB immediately
        dummy_date += timedelta(weeks=1)

        print(f"--- message {i}: {len(report)} result(s) on {report.date} @ {report.event} ---")
        for result in report:
            player_display = result.player or "(unknown)"
            deck_display = result.deck or "(unknown)"
            print(
                f"  {player_display:20} | {deck_display:25} | {str(result.record):6} "
                f"(raw: {result.raw_line!r})"
            )


if __name__ == "__main__":
    main()
