from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as date_

from pymongo.collection import Collection

from .db import get_collection

RECORD_RE = re.compile(r"(?<!\d)([0-3])-([0-3])(?:-([0-3]))?(?!\d)")


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
