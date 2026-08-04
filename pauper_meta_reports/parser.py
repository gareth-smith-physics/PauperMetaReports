from __future__ import annotations

import re
from datetime import date as date_

from .models import RECORD_RE, MetaReport, Record, Result
from .registry import AskCallback, DeckRegistry, LGSRegistry, NameRegistry, normalize

_DELIMITERS = (",", " - ")
_NEW_LGS_RE = re.compile(r"new\s+lgs\s*:\s*(.+)", re.IGNORECASE)


def is_meta_report(message: str, min_results: int = 2) -> bool:
    """Heuristic: a real meta-report message has multiple record-shaped lines."""
    hits = sum(1 for line in message.splitlines() if RECORD_RE.search(line))
    return hits >= min_results


def parse_new_lgs_announcement(message: str) -> str | None:
    """Pull the store name out of a "New LGS: XXXXXX" line, if the message has one."""
    match = _NEW_LGS_RE.search(message)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def find_lgs_in_message(message: str, lgs_registry: LGSRegistry) -> str | None:
    """Search message text for any known LGS name/alias, returning its canonical
    name. Prefers the longest match, so a store whose name is a substring of
    another known store's name doesn't win by accident."""
    normalized_message = normalize(message)
    best_canonical = None
    best_len = 0
    for entry in lgs_registry.entries:
        for name in entry.all_names():
            normalized_name = normalize(name)
            if normalized_name and normalized_name in normalized_message and len(normalized_name) > best_len:
                best_canonical = entry.canonical
                best_len = len(normalized_name)
    return best_canonical


def _split_name_deck(before: str, after: str, name_registry: NameRegistry) -> tuple[str, str]:
    """Split the text surrounding a record into (raw_name, raw_deck).

    `before`/`after` are the line's text before/after the matched record, kept
    separate (rather than blindly concatenated) so that a "Name (Deck)" wrapper
    can be told apart from a deck that merely happens to contain stray parens,
    e.g. "dimir control/terror(?)".
    """
    before = before.strip(" -,")
    after = after.strip()

    if after.startswith("(") and after.endswith(")") and len(after) > 2:
        return before, after[1:-1].strip()

    remainder = f"{before} {after}".strip()
    for delim in _DELIMITERS:
        if delim in remainder:
            name, deck = remainder.split(delim, 1)
            return name.strip(), deck.strip(" ()")

    tokens = remainder.split()
    if not tokens:
        return "", ""

    # No delimiter at all: try the longest known-name prefix first, so a
    # multi-word name ("Gareth S") isn't chopped into name="Gareth", deck="S ...".
    for n in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:n])
        if name_registry.lookup(candidate) is not None:
            return candidate, " ".join(tokens[n:]).strip()

    return tokens[0], " ".join(tokens[1:]).strip()


def parse_result_line(
    line: str,
    date: date_,
    event: str,
    name_registry: NameRegistry,
    deck_registry: DeckRegistry,
    name_ask: AskCallback | None = None,
    deck_ask: AskCallback | None = None,
) -> Result | None:
    line = line.strip()
    if not line or line == "...":
        return None

    match = RECORD_RE.search(line)
    if not match:
        return None

    record = Record.from_match(match)
    before, after = line[: match.start()], line[match.end() :]
    raw_name, raw_deck = _split_name_deck(before, after, name_registry)
    if not raw_name or not raw_deck:
        return None

    return Result(
        player=name_registry.resolve(raw_name, ask=name_ask),
        deck=deck_registry.resolve(raw_deck, ask=deck_ask),
        record=record,
        date=date,
        event=event,
        raw_player=raw_name,
        raw_deck=raw_deck,
        raw_line=line,
    )


def parse_meta_report(
    message: str,
    date: date_,
    event: str,
    name_registry: NameRegistry,
    deck_registry: DeckRegistry,
    name_ask: AskCallback | None = None,
    deck_ask: AskCallback | None = None,
) -> MetaReport:
    report = MetaReport(date=date, event=event)
    for line in message.splitlines():
        result = parse_result_line(
            line, date, event, name_registry, deck_registry, name_ask=name_ask, deck_ask=deck_ask
        )
        if result is not None:
            report.add(result)
    return report
