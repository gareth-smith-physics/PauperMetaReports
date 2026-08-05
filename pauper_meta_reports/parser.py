from __future__ import annotations

import re
from datetime import date as date_

from .models import RECORD_RE, MetaReport, Record, Result
from .registry import AskCallback, DeckRegistry, LGSRegistry, NameRegistry, normalize

_DELIMITERS = (",", " - ")
_NEW_LGS_RE = re.compile(r"new\s+lgs\s*:\s*(.+)", re.IGNORECASE)
_PAREN_WRAP_RE = re.compile(r"^(.*)\(([^()]+)\)$")


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


def _line_remainder(line: str) -> str | None:
    """The line's name/deck text: the record removed, and delimiter-adjacent
    punctuation trimmed off each side before rejoining. None if the line has
    no record at all."""
    match = RECORD_RE.search(line)
    if not match:
        return None
    before = line[: match.start()].strip(" -,/")
    after = line[match.end() :].strip(" -,/")
    return f"{before} {after}".strip()


def _paren_split(remainder: str) -> tuple[str, str] | None:
    """Split into (prefix, interior) if `remainder` ends in exactly one
    well-formed, non-nested parenthetical group wrapping the whole tail -
    e.g. "Spy Combo (Aria)" or "Gareth (Dimir Control)". Which side is the
    name and which is the deck isn't decided here - some messages wrap the
    deck, others wrap the player. The interior must contain a letter, so a
    deck's own incidental punctuation (e.g. "dimir control/terror(?)") isn't
    mistaken for a wrapped name.
    """
    match = _PAREN_WRAP_RE.match(remainder)
    if not match:
        return None
    prefix, interior = match.group(1).strip(), match.group(2).strip()
    if not prefix or not any(c.isalpha() for c in interior):
        return None
    return prefix, interior


def _delimiter_split(remainder: str) -> tuple[str, str] | None:
    """Split on the first "," or " - " found. Order is preserved as-is; the
    caller decides which chunk is the name and which is the deck."""
    for delim in _DELIMITERS:
        if delim in remainder:
            part_a, part_b = remainder.split(delim, 1)
            return part_a.strip(), part_b.strip()
    return None


def _line_chunks(line: str) -> tuple[str, str] | None:
    """The two raw chunks flanking a line's record, in on-the-page order -
    from a parenthetical wrapper or a delimiter, whichever matches. None for
    a line with no record, or no recognizable two-chunk shape (a
    delimiter-less line falls back to a token-based name search instead -
    see _split_name_deck).
    """
    remainder = _line_remainder(line)
    if remainder is None:
        return None
    return _paren_split(remainder) or _delimiter_split(remainder)


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


def _split_name_deck(line: str, name_registry: NameRegistry, swapped: bool) -> tuple[str, str]:
    """Split a line's name/deck text into (raw_name, raw_deck).

    `swapped` says whether this report's field order puts the deck first
    (paren case: "Deck (Name)"; delimiter case: "Deck - Name") rather than
    the default "Name (Deck)"/"Name - Deck" - decided once for the whole
    report by _report_order_is_swapped, not re-judged per line.
    """
    remainder = _line_remainder(line) or ""
    chunks = _paren_split(remainder) or _delimiter_split(remainder)
    if chunks is not None:
        part_a, part_b = chunks
        return (part_b, part_a) if swapped else (part_a, part_b)

    # No delimiter or parens at all: try the longest known-name prefix
    # first, so a multi-word name ("Gareth S") isn't chopped into
    # name="Gareth", deck="S ...".
    tokens = remainder.split()
    if not tokens:
        return "", ""
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

    raw_name, raw_deck = _split_name_deck(line, name_registry, swapped)
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
