from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import DeckRegistry, update_deck_registry_from_goldfish


def main() -> None:
    deck_registry = DeckRegistry()

    print("Fetching deck list from MTGGoldfish...")
    added = update_deck_registry_from_goldfish(deck_registry)

    if added:
        print(f"Added {len(added)} new deck(s):")
        for name in added:
            print(f"  - {name}")
    else:
        print("No new decks found; registry already up to date.")


if __name__ == "__main__":
    main()
