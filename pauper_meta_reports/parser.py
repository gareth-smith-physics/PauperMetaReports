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


def _delimiter_split(remainder: str) -> tuple[str, str] | None:
    """Split `remainder` on the first delimiter found. The order of the two
    chunks relative to the delimiter is preserved as-is; it's up to the
    caller to decide which chunk is the name and which is the deck."""
    for delim in _DELIMITERS:
        if delim in remainder:
            part_a, part_b = remainder.split(delim, 1)
            return part_a.strip(), part_b.strip(" ()")
    return None


def _line_chunks(line: str) -> tuple[str, str] | None:
    """The two raw chunks around a line's record, for lines whose field
    order is ambiguous - i.e. not a "Name (Deck)" wrapper (unambiguous: deck
    is always in the parens) and not delimiter-less (that path already picks
    out the name via a registry lookup). Returns None for anything else,
    including lines with no record at all.
    """
    match = RECORD_RE.search(line)
    if not match:
        return None
    before = line[: match.start()].strip(" -,/")
    after = line[match.end() :].strip(" -,/")
    if after.startswith("(") and after.endswith(")") and len(after) > 2:
        return None
    remainder = f"{before} {after}".strip()
    return _delimiter_split(remainder)


def _order_evidence(
    part_a: str, part_b: str, name_registry: NameRegistry, deck_registry: DeckRegistry
) -> tuple[int, int]:
    """How much registry evidence supports reading (part_a, part_b) as
    (name, deck) vs (deck, name).

    Uses lookup() (thresholded), not raw fuzzy scores: an unrelated brand-new
    name/deck pair still gets *some* nonzero fuzzy score against everything
    in a non-empty registry, so comparing raw scores would flip on pure noise
    whenever both chunks are new. Only a clean match on either side counts as
    evidence.
    """
    default_evidence = int(name_registry.lookup(part_a) is not None) + int(
        deck_registry.lookup(part_b) is not None
    )
    swapped_evidence = int(deck_registry.lookup(part_a) is not None) + int(
        name_registry.lookup(part_b) is not None
    )
    return default_evidence, swapped_evidence


def _report_order_is_swapped(message: str, name_registry: NameRegistry, deck_registry: DeckRegistry) -> bool:
    """A meta report uses one consistent field order for every line, so
    evidence gathered from whichever lines give a clear registry signal
    settles the order for the whole report - including lines whose own name
    and deck are both brand new and would otherwise have nothing to go on.
    """
    total_default = total_swapped = 0
    for line in message.splitlines():
        chunks = _line_chunks(line)
        if chunks is None:
            continue
        default_evidence, swapped_evidence = _order_evidence(*chunks, name_registry, deck_registry)
        total_default += default_evidence
        total_swapped += swapped_evidence
    return total_swapped > total_default


def _split_name_deck(before: str, after: str, name_registry: NameRegistry, swapped: bool) -> tuple[str, str]:
    """Split the text surrounding a record into (raw_name, raw_deck).

    `before`/`after` are the line's text before/after the matched record, kept
    separate (rather than blindly concatenated) so that a "Name (Deck)" wrapper
    can be told apart from a deck that merely happens to contain stray parens,
    e.g. "dimir control/terror(?)".

    `swapped` says whether this report's field order is "Deck - Name" rather
    than the default "Name - Deck" - decided once for the whole report by
    _report_order_is_swapped, not re-judged per line.
    """
    before = before.strip(" -,/")
    after = after.strip(" -,/")

    if after.startswith("(") and after.endswith(")") and len(after) > 2:
        return before, after[1:-1].strip()

    remainder = f"{before} {after}".strip()
    chunks = _delimiter_split(remainder)
    if chunks is not None:
        part_a, part_b = chunks
        return (part_b, part_a) if swapped else (part_a, part_b)

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
    swapped: bool = False,
) -> Result | None:
    line = line.strip()
    if not line or line == "...":
        return None

    match = RECORD_RE.search(line)
    if not match:
        return None

    record = Record.from_match(match)
    before, after = line[: match.start()], line[match.end() :]
    raw_name, raw_deck = _split_name_deck(before, after, name_registry, swapped)
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
    swapped = _report_order_is_swapped(message, name_registry, deck_registry)
    for line in message.splitlines():
        result = parse_result_line(
            line,
            date,
            event,
            name_registry,
            deck_registry,
            name_ask=name_ask,
            deck_ask=deck_ask,
            swapped=swapped,
        )
        if result is not None:
            report.add(result)
    return report
