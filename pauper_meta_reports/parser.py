from __future__ import annotations

import re
from datetime import date as date_

from .models import RECORD_RE, MetaReport, Record, Result
from .registry import AskCallback, DeckRegistry, LGSRegistry, NameRegistry, normalize

_NEW_LGS_RE = re.compile(r"new\s+lgs\s*:\s*(.+)", re.IGNORECASE)
_SEGMENT_SPLIT_RE = re.compile(r"[,/:\-()]+")


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


def _line_segments(line: str) -> list[str] | None:
    """Split a record-bearing line into its non-record segments, in
    on-the-page order. Any run of "," "/" "-" ":" "(" or ")" counts as a
    delimiter - interchangeably, in any combination, with or without
    surrounding spaces - so "A / B - C", "A: B (C)", "A/B/C" and "A (B) C"
    all reduce to the same three segments. The record's own dash is never
    at risk of being caught up in this: RECORD_RE (with its own
    digit-adjacency guard) is matched first, and only the text on either
    side of *that* match is split, never the record itself.

    None if the line has no record at all.
    """
    match = RECORD_RE.search(line)
    if not match:
        return None
    before = _SEGMENT_SPLIT_RE.split(line[: match.start()])
    after = _SEGMENT_SPLIT_RE.split(line[match.end() :])
    return [s.strip() for s in before + after if s.strip()]


def _line_chunks(line: str) -> tuple[str, str] | None:
    """The name and deck segments flanking a line's record, in on-the-page
    order - only when the line splits *cleanly* into exactly two non-record
    segments. Any other count means the delimiters weren't unambiguous (no
    delimiter at all, or a name/deck's own incidental punctuation added an
    extra split) - see _split_name_deck's token-based fallback for that case.
    """
    segments = _line_segments(line)
    if segments is None or len(segments) != 2:
        return None
    return segments[0], segments[1]


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

    `swapped` says whether this report's field order puts the deck segment
    first rather than the name segment - decided once for the whole report
    by _report_order_is_swapped, not re-judged per line.
    """
    chunks = _line_chunks(line)
    if chunks is not None:
        part_a, part_b = chunks
        return (part_b, part_a) if swapped else (part_a, part_b)

    # Not a clean two-segment split (no delimiters at all, or more than two
    # segments once a name/deck's own incidental punctuation is accounted
    # for - e.g. "dimir control/terror(?)" or "Mono-White Heroic"): fall
    # back to the longest known-name prefix, so a multi-word name
    # ("Gareth S") isn't chopped into name="Gareth", deck="S ...".
    remainder = _line_remainder(line) or ""
    # A leftover delimiter (e.g. the "-" between "Gareth" and "dimir
    # control/terror(?)") can survive as its own whitespace-separated token
    # once _line_chunks has declined to split this line - drop anything
    # that's pure punctuation, since a real name/deck token always has at
    # least one letter or digit in it.
    tokens = [t for t in remainder.split() if any(c.isalnum() for c in t)]
    if not tokens:
        return "", ""
    for n in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:n])
        if name_registry.lookup(candidate) is not None:
            return candidate, " ".join(tokens[n:]).strip()
    return tokens[0], " ".join(tokens[1:]).strip()


def _line_has_known_name_and_deck(line: str, name_registry: NameRegistry, deck_registry: DeckRegistry) -> bool:
    """Whether this line's name/deck split turns up a recognizable player
    AND a recognizable deck, in either field order. Used to tell a genuine
    meta-report result apart from a line that's merely record-shaped - e.g.
    a round-by-round trophy post ("2-0 vs UB Terror") has no player name on
    the line at all, and shouldn't be mistaken for one.
    """
    for swapped in (False, True):
        raw_name, raw_deck = _split_name_deck(line, name_registry, swapped)
        if raw_name and raw_deck and name_registry.lookup(raw_name) and deck_registry.lookup(raw_deck):
            return True
    return False


def is_meta_report(
    message: str,
    name_registry: NameRegistry,
    deck_registry: DeckRegistry,
    min_results: int = 2,
) -> bool:
    """Heuristic: a real meta-report message has multiple lines that are
    both record-shaped AND identifiably a known player's known deck - not
    just any line that happens to contain a "W-L" pattern. Falls back to
    the plain record-count check when either registry is still empty (e.g.
    the very first sync ever, with nothing yet to cross-check against).
    """
    lines = message.splitlines()
    if not name_registry.entries or not deck_registry.entries:
        return sum(1 for line in lines if RECORD_RE.search(line)) >= min_results

    hits = sum(
        1
        for line in lines
        if RECORD_RE.search(line) and _line_has_known_name_and_deck(line, name_registry, deck_registry)
    )
    return hits >= min_results


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
