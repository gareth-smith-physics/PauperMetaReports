from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as date_
from pathlib import Path

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


@dataclass
class History:
    """The accumulated set of meta reports already parsed, persisted to disk
    so a future run can tell which reports it's already analyzed and skip
    them instead of reprocessing the same Discord messages.
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
        use this to skip re-analyzing a meta report they've already seen.
        """
        if self.has_report(report.date, report.event):
            return False
        self.reports.append(report)
        return True

    def __iter__(self):
        return iter(self.reports)

    def __len__(self) -> int:
        return len(self.reports)

    def to_dict(self) -> dict:
        return {"reports": [r.to_dict() for r in self.reports]}

    @classmethod
    def from_dict(cls, data: dict) -> "History":
        return cls(reports=[MetaReport.from_dict(r) for r in data.get("reports", [])])

    @classmethod
    def load(cls, path: Path | str) -> "History":
        path = Path(path)
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))

    def save(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
