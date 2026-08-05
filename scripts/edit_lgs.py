from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz, process

from pauper_meta_reports import History, LGSRegistry
from pauper_meta_reports.registry import AliasEntry, normalize


def find_entry(registry: LGSRegistry, raw: str) -> AliasEntry | None:
    for entry in registry.entries:
        if normalize(entry.canonical) == normalize(raw):
            return entry
        if any(normalize(a) == normalize(raw) for a in entry.aliases):
            return entry

    candidates = {normalize(n): entry for entry in registry.entries for n in entry.all_names()}
    if candidates:
        match = process.extractOne(normalize(raw), candidates.keys(), scorer=fuzz.ratio)
        if match is not None:
            matched_key, score, _ = match
            if score >= 70.0:
                entry = candidates[matched_key]
                confirm = input(f"Did you mean **{entry.canonical}** (score {score:.0f})? [y/n]: ").strip().lower()
                if confirm in ("y", "yes"):
                    return entry

    print(f"\nNo LGS matching '{raw}'. Known LGSs:")
    for i, entry in enumerate(registry.entries, start=1):
        print(f"  [{i}] {entry.canonical}")
    choice = input("Pick a number, or press Enter to abort: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(registry.entries):
        return registry.entries[int(choice) - 1]
    return None


def show_entry(entry: AliasEntry) -> None:
    print(f"\nLGS: {entry.canonical}")
    if entry.aliases:
        print("Aliases:")
        for i, alias in enumerate(entry.aliases, start=1):
            print(f"  [{i}] {alias}")
    else:
        print("Aliases: (none)")


def rename(registry: LGSRegistry, history: History, entry: AliasEntry) -> None:
    new_name = input("New canonical name: ").strip()
    if not new_name:
        print("No name entered - not renamed.")
        return
    conflict = registry.find_conflict(entry, new_name)
    if conflict is not None:
        print(f"'{new_name}' already belongs to LGS '{conflict.canonical}' - not renamed.")
        return

    old_name = entry.canonical
    registry.rename_canonical(old_name, new_name)
    updated = history.rename_event(old_name, new_name)
    print(f"Renamed '{old_name}' -> '{new_name}' ({updated} past report(s) updated).")


def add_alias(registry: LGSRegistry, entry: AliasEntry) -> None:
    alias = input("New alias: ").strip()
    if not alias:
        print("No alias entered.")
        return
    conflict = registry.find_conflict(entry, alias)
    if conflict is not None:
        print(f"'{alias}' already belongs to LGS '{conflict.canonical}' - not added.")
        return
    if registry.add_alias(entry.canonical, alias):
        print(f"Added alias '{alias}' to '{entry.canonical}'.")
    else:
        print("Already an alias of this LGS - nothing to do.")


def remove_alias(registry: LGSRegistry, entry: AliasEntry) -> None:
    if not entry.aliases:
        print("This LGS has no aliases to remove.")
        return
    choice = input("Remove which alias? (number): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(entry.aliases)):
        print("Not a valid choice - nothing removed.")
        return
    alias = entry.aliases[int(choice) - 1]
    registry.remove_alias(entry.canonical, alias)
    print(f"Removed alias '{alias}' from '{entry.canonical}'.")


def main() -> None:
    registry = LGSRegistry()
    history = History.load()

    raw = input("LGS to edit: ").strip()
    if not raw:
        print("No LGS entered - aborting.")
        return
    entry = find_entry(registry, raw)
    if entry is None:
        print("Aborting.")
        return

    while True:
        show_entry(entry)
        choice = input(
            "\n[1] Rename LGS  [2] Add alias  [3] Remove alias  [Enter] Done: "
        ).strip()
        if choice == "1":
            rename(registry, history, entry)
        elif choice == "2":
            add_alias(registry, entry)
        elif choice == "3":
            remove_alias(registry, entry)
        else:
            break


if __name__ == "__main__":
    main()
