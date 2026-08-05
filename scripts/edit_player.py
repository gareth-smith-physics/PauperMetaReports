from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz, process

from pauper_meta_reports import History, NameRegistry, ask_queue_for_review
from pauper_meta_reports.registry import AliasEntry, normalize


def find_entry(registry: NameRegistry, raw: str) -> AliasEntry | None:
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

    print(f"\nNo player matching '{raw}'. Known players:")
    for i, entry in enumerate(registry.entries, start=1):
        print(f"  [{i}] {entry.canonical}")
    choice = input("Pick a number, or press Enter to abort: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(registry.entries):
        return registry.entries[int(choice) - 1]
    return None


def show_entry(entry: AliasEntry) -> None:
    print(f"\nPlayer: {entry.canonical}")
    if entry.aliases:
        print("Aliases:")
        for i, alias in enumerate(entry.aliases, start=1):
            print(f"  [{i}] {alias}")
    else:
        print("Aliases: (none)")


def rename(registry: NameRegistry, history: History, entry: AliasEntry) -> None:
    new_name = input("New canonical name: ").strip()
    if not new_name:
        print("No name entered - not renamed.")
        return
    conflict = registry.find_conflict(entry, new_name)
    if conflict is not None:
        print(f"'{new_name}' already belongs to player '{conflict.canonical}' - not renamed.")
        return

    old_name = entry.canonical
    registry.rename_canonical(old_name, new_name)
    # Keep first/last in sync with the new display name (same split _add_new
    # uses) - NameRegistry's first/last-name disambiguation groups players
    # by `first`, not `canonical`, so a future same-first-name player would
    # otherwise group against stale metadata.
    entry.first, _, entry.last = new_name.partition(" ")
    registry.save()
    updated = history.rename_player(old_name, new_name)
    print(f"Renamed '{old_name}' -> '{new_name}' ({updated} past result(s) updated).")


def add_alias(registry: NameRegistry, entry: AliasEntry) -> None:
    alias = input("New alias: ").strip()
    if not alias:
        print("No alias entered.")
        return
    conflict = registry.find_conflict(entry, alias)
    if conflict is not None:
        print(f"'{alias}' already belongs to player '{conflict.canonical}' - not added.")
        return
    if registry.add_alias(entry.canonical, alias):
        print(f"Added alias '{alias}' to '{entry.canonical}'.")
    else:
        print("Already an alias of this player - nothing to do.")


def remove_alias(registry: NameRegistry, entry: AliasEntry) -> None:
    if not entry.aliases:
        print("This player has no aliases to remove.")
        return
    choice = input("Remove which alias? (number): ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(entry.aliases)):
        print("Not a valid choice - nothing removed.")
        return
    alias = entry.aliases[int(choice) - 1]
    registry.remove_alias(entry.canonical, alias)
    print(f"Removed alias '{alias}' from '{entry.canonical}'.")


def delete_player(registry: NameRegistry, history: History, entry: AliasEntry) -> bool:
    """Remove this player from the registry entirely, and reset every past
    result of theirs back to unresolved - queued for review via the
    Streamlit "Unresolved" tab, the same as any other ambiguous name -
    rather than silently leaving history pointing at a canonical name that
    no longer exists.

    Returns True if the player was deleted (the caller should stop editing
    this now-gone entry).
    """
    canonical = entry.canonical
    print(
        f"\nThis will delete '{canonical}' and send every one of their past "
        "results back to the unresolved queue for review."
    )
    confirm = input("Proceed? [y/n]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborted - nothing deleted.")
        return False

    affected = history.unresolve_player(canonical)
    for result in affected:
        ask = ask_queue_for_review("names", result.date, result.event)
        ask(result.raw_player, None, 0.0, None)

    registry.delete(canonical)
    print(f"Deleted '{canonical}' - {len(affected)} result(s) reset to unresolved and queued for review.")
    return True


def merge(registry: NameRegistry, history: History, entry: AliasEntry) -> None:
    """Merge another player into this one: the other player's name and
    aliases become aliases of `entry`, and every past result of theirs is
    reattributed to `entry`."""
    raw = input(f"Merge which player into '{entry.canonical}'? (they'll be absorbed): ").strip()
    if not raw:
        print("No player entered - aborting merge.")
        return
    other = find_entry(registry, raw)
    if other is None:
        print("Aborting.")
        return
    if other is entry:
        print("That's the same player - nothing to merge.")
        return

    absorbed_names = other.all_names()
    old_canonical = other.canonical
    print(f"\nThis will merge '{old_canonical}' into '{entry.canonical}':")
    print(f"  - '{old_canonical}' and its {len(other.aliases)} alias(es) become aliases of '{entry.canonical}'")
    print(f"  - past results credited to '{old_canonical}' will be recredited to '{entry.canonical}'")
    confirm = input("Proceed? [y/n]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborted - nothing merged.")
        return

    registry.delete(old_canonical)
    for name in absorbed_names:
        registry.add_alias(entry.canonical, name)
    updated = history.rename_player(old_canonical, entry.canonical)
    print(f"Merged '{old_canonical}' into '{entry.canonical}' ({updated} past result(s) updated).")


def main() -> None:
    registry = NameRegistry()
    history = History.load()

    raw = input("Player to edit: ").strip()
    if not raw:
        print("No player entered - aborting.")
        return
    entry = find_entry(registry, raw)
    if entry is None:
        print("Aborting.")
        return

    while True:
        show_entry(entry)
        choice = input(
            "\n[1] Rename player  [2] Add alias  [3] Remove alias  "
            "[4] Delete player  [5] Merge another player in  [Enter] Done: "
        ).strip()
        if choice == "1":
            rename(registry, history, entry)
        elif choice == "2":
            add_alias(registry, entry)
        elif choice == "3":
            remove_alias(registry, entry)
        elif choice == "4":
            if delete_player(registry, history, entry):
                break
        elif choice == "5":
            merge(registry, history, entry)
        else:
            break


if __name__ == "__main__":
    main()
