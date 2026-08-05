from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import get_collection


def main() -> None:
    history = get_collection("history")
    unresolved = get_collection("unresolved")
    history_count = history.count_documents({})
    unresolved_count = unresolved.count_documents({})

    if history_count == 0 and unresolved_count == 0:
        print("History and unresolved are already empty - nothing to do.")
        return

    print(
        f"This will permanently delete all {history_count} report(s) from history "
        f"and all {unresolved_count} pending item(s) from unresolved."
    )
    print("The names/decks/lgs registries are NOT affected.")
    confirm = input("Type 'delete' to confirm: ").strip()

    if confirm != "delete":
        print("Aborted - nothing was deleted.")
        return

    history_result = history.delete_many({})
    unresolved_result = unresolved.delete_many({})
    print(f"Deleted {history_result.deleted_count} report(s) and {unresolved_result.deleted_count} pending item(s).")


if __name__ == "__main__":
    main()
