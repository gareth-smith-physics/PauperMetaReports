from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import fuzz, process

from pauper_meta_reports import DeckRegistry, History, Record, ask_terminal
from pauper_meta_reports.models import MetaReport, Result


def pick_report(history: History, report_date: date) -> MetaReport | None:
    reports = [r for r in history.reports if r.date == report_date]
    if not reports:
        print(f"No meta report found for {report_date}.")
        return None
    if len(reports) == 1:
        return reports[0]

    print(f"\nMultiple reports found for {report_date}:")
    for i, report in enumerate(reports, start=1):
        print(f"  [{i}] {report.event}")
    choice = input("Which one? (number, or type the venue name): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(reports):
        return reports[int(choice) - 1]
    for report in reports:
        if report.event.lower() == choice.lower():
            return report
    print("No match - aborting.")
    return None


def pick_result(report: MetaReport, raw_player: str) -> Result | None:
    for result in report.results:
        if result.player and result.player.lower() == raw_player.lower():
            return result

    named = [r for r in report.results if r.player]
    if named:
        match = process.extractOne(raw_player, [r.player for r in named], scorer=fuzz.ratio)
        if match is not None:
            matched_name, score, index = match
            if score >= 70.0:
                confirm = input(f"Did you mean **{matched_name}** (score {score:.0f})? [y/n]: ").strip().lower()
                if confirm in ("y", "yes"):
                    return named[index]

    print(f"\nNo player matching '{raw_player}' in this report. Players in this report:")
    for i, result in enumerate(report.results, start=1):
        print(f"  [{i}] {result.player or '(unresolved)'} - {result.deck or '(unresolved)'} - {result.record}")
    choice = input("Pick a number, or press Enter to abort: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(report.results):
        return report.results[int(choice) - 1]
    return None


def main() -> None:
    date_input = input("Report date (YYYY-MM-DD): ").strip()
    try:
        report_date = date.fromisoformat(date_input)
    except ValueError:
        print("Not a valid date - aborting.")
        return

    history = History.load()
    report = pick_report(history, report_date)
    if report is None:
        return

    raw_player = input("Player name: ").strip()
    if not raw_player:
        print("No player entered - aborting.")
        return

    result = pick_result(report, raw_player)
    if result is None:
        print("Aborting.")
        return

    print(f"\nCurrent entry: {result.player} | {result.deck or '(unresolved)'} | {result.record}")
    confirm = input("Edit this entry? [y/n]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborting.")
        return

    new_deck_raw = input("New deck: ").strip()
    if not new_deck_raw:
        print("No deck entered - aborting.")
        return
    deck_registry = DeckRegistry()
    new_deck = deck_registry.resolve(new_deck_raw, ask=ask_terminal("deck", allow_skip=False))

    new_record_raw = input("New record (e.g. 2-1 or 2-1-1): ").strip()
    try:
        new_record = Record.parse(new_record_raw)
    except ValueError as e:
        print(f"{e} - aborting.")
        return

    old_deck, old_record = result.deck, result.record
    result.deck = new_deck
    result.raw_deck = new_deck_raw
    result.record = new_record
    history.save_report(report)

    print(
        f"\nUpdated {result.player} @ {report.event} ({report.date}): "
        f"{old_deck or '(unresolved)'} {old_record} -> {new_deck} {new_record}"
    )


if __name__ == "__main__":
    main()
