from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_

from pymongo.collection import Collection

from .db import get_collection

# Wins/losses/draws each 0-9 (a single digit) - wide enough for any
# realistically-sized Swiss event (e.g. "4-0" from a 4-round event with an
# undefeated record), not just the 3-round-cap events seen in early data.
# The separator is "-" or plain whitespace ("3 0" as well as "3-0") - safe
# to be this loose because is_meta_report() no longer just checks for a
# record-shaped line, it cross-checks against the name/deck registries too,
# so a stray "digit space digit" elsewhere in ordinary chat isn't enough on
# its own to make a message look like a meta report.
RECORD_RE = re.compile(r"(?<!\d)([0-9])[-\s]+([0-9])(?:[-\s]+([0-9]))?(?!\d)")


@dataclass(frozen=True)
class Record:
    wins: int
    losses: int
    draws: int = 0

    @classmethod
    def from_match(cls, match: re.Match) -> "Record":
        wins, losses, draws = match.groups()
        return cls(int(wins), int(losses), int(draws) if draws else 0)

    @classmethod
    def parse(cls, text: str) -> "Record":
        match = RECORD_RE.search(text)
        if not match:
            raise ValueError(f"No record found in {text!r}")
        return cls.from_match(match)

    def __str__(self) -> str:
        if self.draws:
            return f"{self.wins}-{self.losses}-{self.draws}"
        return f"{self.wins}-{self.losses}"

    @property
    def score(self) -> int:
        """Standings points: 3 per win, 1 per draw, 0 per loss."""
        return 3 * self.wins + self.draws

    def to_dict(self) -> dict:
        return {"wins": self.wins, "losses": self.losses, "draws": self.draws}

    @classmethod
    def from_dict(cls, data: dict) -> "Record":
        return cls(wins=data["wins"], losses=data["losses"], draws=data.get("draws", 0))


@dataclass
class Result:
    # None means a human explicitly skipped identifying this player/deck
    # (e.g. an unparseable raw deck name) rather than guessing.
    player: str | None
    deck: str | None
    record: Record
    date: date_
    event: str
    raw_player: str = ""
    raw_deck: str = ""
    raw_line: str = ""

    def to_dict(self) -> dict:
        return {
            "player": self.player,
            "deck": self.deck,
            "record": self.record.to_dict(),
            "date": self.date.isoformat(),
            "event": self.event,
            "raw_player": self.raw_player,
            "raw_deck": self.raw_deck,
            "raw_line": self.raw_line,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Result":
        return cls(
            player=data["player"],
            deck=data["deck"],
            record=Record.from_dict(data["record"]),
            date=date_.fromisoformat(data["date"]),
            event=data["event"],
            raw_player=data.get("raw_player", ""),
            raw_deck=data.get("raw_deck", ""),
            raw_line=data.get("raw_line", ""),
        )


@dataclass
class MetaReport:
    date: date_
    event: str
    results: list[Result] = field(default_factory=list)

    def add(self, result: Result) -> None:
        self.results.append(result)

    def __iter__(self):
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "event": self.event,
            "results": [r.to_dict() for r in self.results],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MetaReport":
        return cls(
            date=date_.fromisoformat(data["date"]),
            event=data["event"],
            results=[Result.from_dict(r) for r in data["results"]],
        )


def _report_doc(report: MetaReport) -> dict:
    doc = report.to_dict()
    # (date, event) is already the application-level uniqueness key
    # (has_report()); using it as the Mongo _id makes that a DB-level
    # guarantee too, and makes re-syncing the same report an idempotent upsert.
    doc["_id"] = f"{doc['date']}::{doc['event']}"
    return doc


@dataclass
class History:
    """The accumulated set of meta reports already parsed, persisted to
    MongoDB so a future run can tell which reports it's already analyzed and
    skip them instead of reprocessing the same Discord messages.
    """

    reports: list[MetaReport] = field(default_factory=list)

    @property
    def first_date(self) -> date_ | None:
        return min((r.date for r in self.reports), default=None)

    @property
    def last_date(self) -> date_ | None:
        return max((r.date for r in self.reports), default=None)

    def has_report(self, date: date_, event: str) -> bool:
        return any(r.date == date and r.event == event for r in self.reports)

    def add(self, report: MetaReport) -> bool:
        """Record a report unless one for this date+event is already known.

        Returns True if it was added, False if it was a repeat - callers can
        use this to skip re-analyzing a meta report they've already seen. If
        this History came from load(), the new report is persisted immediately.
        """
        if self.has_report(report.date, report.event):
            return False
        self.reports.append(report)
        collection = getattr(self, "_collection", None)
        if collection is not None:
            doc = _report_doc(report)
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return True

    def backfill(self, field: str, raw_field: str, raw: str, canonical: str) -> int:
        """Retroactively fill in `field` (player/deck) wherever it was left
        unresolved (None) with exactly this raw text. Used once a queued
        review item is finally answered, so past reports reflect the answer
        instead of staying stuck with an unknown player/deck forever.

        Returns how many results were updated.
        """
        collection = getattr(self, "_collection", None)
        updated = 0
        for report in self.reports:
            changed = False
            for result in report.results:
                if getattr(result, raw_field) == raw and getattr(result, field) is None:
                    setattr(result, field, canonical)
                    changed = True
                    updated += 1
            if changed and collection is not None:
                doc = _report_doc(report)
                collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return updated

    def backfill_event(self, date: date_, old_event: str, new_event: str) -> int:
        """Retroactively correct a report's venue - and every one of its
        results - from a placeholder (e.g. a default used because no known
        LGS was mentioned in the message) to the real one.

        The report's Mongo _id is derived from date+event, so this can't be
        a simple field update: the old document (under the old _id) has to
        be deleted and a new one written under the new _id, or the old
        placeholder document would be left behind as an orphaned duplicate.

        Returns how many results were updated (0, or the report's full count).
        """
        collection = getattr(self, "_collection", None)
        updated = 0
        for report in self.reports:
            if report.date == date and report.event == old_event:
                old_id = f"{report.date.isoformat()}::{report.event}"
                report.event = new_event
                for result in report.results:
                    result.event = new_event
                    updated += 1
                if collection is not None:
                    collection.delete_one({"_id": old_id})
                    doc = _report_doc(report)
                    collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return updated

    def rename_event(self, old_event: str, new_event: str) -> int:
        """Retroactively rename a venue across every past report that used
        it, regardless of date - used after correcting an LGS's canonical
        name in the registry, so history stays consistent with the registry
        instead of pointing at a name that no longer exists there.

        Same _id-is-derived-from-date+event caveat as backfill_event: each
        matching report has to be deleted under its old _id and reinserted
        under the new one, or the old document would be left behind as an
        orphaned duplicate.

        Returns how many reports were updated.
        """
        collection = getattr(self, "_collection", None)
        updated = 0
        for report in self.reports:
            if report.event == old_event:
                old_id = f"{report.date.isoformat()}::{report.event}"
                report.event = new_event
                for result in report.results:
                    result.event = new_event
                if collection is not None:
                    collection.delete_one({"_id": old_id})
                    doc = _report_doc(report)
                    collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
                updated += 1
        return updated

    def rename_deck(self, old_deck: str, new_deck: str) -> int:
        """Retroactively rename a deck across every past result - used after
        correcting a deck's canonical name in the registry, so history stays
        consistent with the registry instead of pointing at a name that no
        longer exists there.

        Returns how many results were updated.
        """
        collection = getattr(self, "_collection", None)
        updated = 0
        for report in self.reports:
            changed = False
            for result in report.results:
                if result.deck == old_deck:
                    result.deck = new_deck
                    changed = True
                    updated += 1
            if changed and collection is not None:
                doc = _report_doc(report)
                collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return updated

    def rename_player(self, old_player: str, new_player: str) -> int:
        """Retroactively rename a player across every past result - used
        after correcting a player's canonical name in the registry, so
        history stays consistent with the registry instead of pointing at a
        name that no longer exists there.

        Returns how many results were updated.
        """
        collection = getattr(self, "_collection", None)
        updated = 0
        for report in self.reports:
            changed = False
            for result in report.results:
                if result.player == old_player:
                    result.player = new_player
                    changed = True
                    updated += 1
            if changed and collection is not None:
                doc = _report_doc(report)
                collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return updated

    def unresolve_player(self, player: str) -> list[Result]:
        """Reset every past result whose player matches `player` back to
        unresolved (None) - used when deleting a player from the registry
        entirely, so their past results go back to needing review instead of
        silently keeping a stale canonical name that no longer exists there.

        Returns the affected Result objects (each now with player=None), so
        the caller can queue each one for review using its own raw text,
        date, and event - queueing itself is outside History's job, same as
        everywhere else in this codebase (see interactive.py).
        """
        collection = getattr(self, "_collection", None)
        affected: list[Result] = []
        for report in self.reports:
            changed = False
            for result in report.results:
                if result.player == player:
                    result.player = None
                    changed = True
                    affected.append(result)
            if changed and collection is not None:
                doc = _report_doc(report)
                collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        return affected

    def add_missing_results(self, date: date_, event: str, candidate_results: list[Result]) -> int:
        """For an already-recorded report, add any of `candidate_results`
        whose raw_line isn't already present among its existing results -
        used when re-parsing a message with newer/fixed parser logic turns
        up lines that failed to parse (and were silently dropped) under
        whatever logic was active when the report was first recorded, even
        though the report itself already exists.

        Matches purely on raw_line, so this only ever *adds* missing lines -
        it won't correct a result that's already there but was parsed
        wrong under old logic (same raw_line, wrong player/deck); that
        needs a targeted fix instead (e.g. scripts/edit_result.py).

        Returns how many results were added. 0 (no-op) if no report exists
        yet for this date+event - the caller should use add() for that case.
        """
        report = next((r for r in self.reports if r.date == date and r.event == event), None)
        if report is None:
            return 0
        existing_raw_lines = {r.raw_line for r in report.results}
        added = 0
        for result in candidate_results:
            if result.raw_line not in existing_raw_lines:
                report.results.append(result)
                existing_raw_lines.add(result.raw_line)
                added += 1
        if added:
            self.save_report(report)
        return added

    def save_report(self, report: MetaReport) -> None:
        """Persist a single report after an in-place edit to one of its
        results (e.g. correcting a misreported deck). `report` must already
        be one of self.reports - this only re-upserts its document."""
        collection = getattr(self, "_collection", None)
        if collection is not None:
            doc = _report_doc(report)
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    def __iter__(self):
        return iter(self.reports)

    def __len__(self) -> int:
        return len(self.reports)

    @classmethod
    def load(cls, collection: Collection | None = None) -> "History":
        collection = collection if collection is not None else get_collection("history")
        reports = [MetaReport.from_dict(d) for d in collection.find({})]
        history = cls(reports=reports)
        history._collection = collection
        return history

    def save(self, collection: Collection | None = None) -> None:
        """Bulk-upsert every report. Only needed for one-off imports/migrations -
        normal incremental use persists automatically via add()."""
        collection = collection if collection is not None else getattr(self, "_collection", None)
        if collection is None:
            raise ValueError("No collection to save to - pass one, or call load() first.")
        for report in self.reports:
            doc = _report_doc(report)
            collection.replace_one({"_id": doc["_id"]}, doc, upsert=True)
