from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import get_collection
from pauper_meta_reports.models import Record


def pick_report(history_coll, report_date: date) -> dict | None:
    docs = list(history_coll.find({"date": report_date.isoformat()}))
    if not docs:
        print(f"No meta report found for {report_date}.")
        return None
    if len(docs) == 1:
        return docs[0]

    print(f"\nMultiple reports found for {report_date}:")
    for i, doc in enumerate(docs, start=1):
        print(f"  [{i}] {doc['event']}")
    choice = input("Which one? (number, or type the venue name): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(docs):
        return docs[int(choice) - 1]
    for doc in docs:
        if doc["event"].lower() == choice.lower():
            return doc
    print("No match - aborting.")
    return None


def find_associated_unresolved(unresolved_coll, doc: dict) -> list[dict]:
    """Everything in the unresolved queue that traces back to this specific
    report. Matched by the report's own raw player/deck text (mirroring
    exactly what History.backfill() checks), not by the queue item's own
    stored `date`/`event` stamp - a venue rename via the "Unresolved" tab's
    LGS resolution flow updates the report itself (backfill_event) but
    doesn't touch older names/decks queue entries still stamped with the
    pre-rename placeholder venue, so matching on that stamp alone would
    silently miss them.
    """
    raw_players = {r["raw_player"] for r in doc["results"] if r.get("player") is None and r.get("raw_player")}
    raw_decks = {r["raw_deck"] for r in doc["results"] if r.get("deck") is None and r.get("raw_deck")}

    matched = []
    for item in unresolved_coll.find({"date": doc["date"]}):
        registry = item["registry"]
        if registry == "lgs" and item["raw"] == doc["date"]:
            matched.append(item)
        elif registry == "names" and item["raw"] in raw_players:
            matched.append(item)
        elif registry == "decks" and item["raw"] in raw_decks:
            matched.append(item)
    return matched


def main() -> None:
    date_input = input("Report date (YYYY-MM-DD): ").strip()
    try:
        report_date = date.fromisoformat(date_input)
    except ValueError:
        print("Not a valid date - aborting.")
        return

    history_coll = get_collection("history")
    unresolved_coll = get_collection("unresolved")

    doc = pick_report(history_coll, report_date)
    if doc is None:
        return

    event = doc["event"]
    print(f"\nReport: {doc['date']} @ {event} - {len(doc['results'])} result(s)")
    for r in doc["results"]:
        player = r.get("player") or "(unresolved)"
        deck = r.get("deck") or "(unresolved)"
        print(f"  {player:20} | {deck:25} | {Record.from_dict(r['record'])}")

    unresolved_docs = find_associated_unresolved(unresolved_coll, doc)
    if unresolved_docs:
        print(f"\n{len(unresolved_docs)} associated unresolved item(s) will also be deleted:")
        for u in unresolved_docs:
            if u["registry"] == "lgs":
                print(f"  [lgs] missing venue - placeholder '{u['event']}'")
            else:
                print(f"  [{u['registry']}] '{u['raw']}'")

    print(
        f"\nThis will permanently delete the report for {doc['date']} @ {event}"
        f" and {len(unresolved_docs)} associated unresolved item(s)."
    )
    confirm = input("Type 'delete' to confirm: ").strip()
    if confirm != "delete":
        print("Aborted - nothing was deleted.")
        return

    history_coll.delete_one({"_id": doc["_id"]})
    if unresolved_docs:
        unresolved_coll.delete_many({"_id": {"$in": [u["_id"] for u in unresolved_docs]}})

    print(f"Deleted report {doc['date']} @ {event} and {len(unresolved_docs)} associated unresolved item(s).")


if __name__ == "__main__":
    main()
