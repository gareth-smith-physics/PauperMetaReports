from __future__ import annotations

import re
from datetime import date as date_

from .models import RECORD_RE, MetaReport, Record, Result
from .registry import AskCallback, DeckRegistry, LGSRegistry, NameRegistry, normalize

_NEW_LGS_RE = re.compile(r"new\s+lgs\s*:\s*(.+)", re.IGNORECASE)
# Candidate field-separating characters. Which of these a given report
# actually uses is inferred per report (see _report_delimiters) - a
# character that only shows up once or twice, incidentally, isn't treated
# as a delimiter just because it's in this list.
_CANDIDATE_DELIMITERS = ",/:()-"


def _delimiter_regex(active: str) -> re.Pattern:
    """Build a split regex using only the delimiter characters in `active`.
    A "-" is only ever treated as a delimiter when it isn't touching a
    digit on either side, regardless of whether it's active, so a record's
    own dash (e.g. the one in "2-1") is never split."""
    parts = []
    others = "".join(c for c in active if c != "-")
    if others:
        parts.append(f"[{re.escape(others)}]+")
    if "-" in active:
        parts.append(r"(?<!\d)-(?!\d)")
    return re.compile("|".join(parts)) if parts else re.compile(r"(?!)")  # matches nothing


def _report_delimiters(message: str, threshold: float = 0.5) -> str:
    """Which candidate delimiter characters this report actually uses to
    separate fields - whichever ones show up outside the record on at
    least `threshold` of its record-bearing lines. A character used on only
    a small minority of lines - e.g. parentheses around an unrelated aside
    like "(Winner of Ornithopter!)" on one line, when nothing else in the
    report ever uses parens - is treated as literal text everywhere in this
    report, not a delimiter, even on the line where it happens to appear.
    """
    texts = []
    for line in message.splitlines():
        match = RECORD_RE.search(line)
        if match is not None:
            texts.append(line[: match.start()] + line[match.end() :])
    if not texts:
        return _CANDIDATE_DELIMITERS
    return "".join(
        c for c in _CANDIDATE_DELIMITERS if sum(1 for t in texts if c in t) >= len(texts) * threshold
    )


def is_meta_report(
    message: str,
    name_registry: NameRegistry,
    deck_registry: DeckRegistry,
    min_results: int = 2,
) -> bool:
    """Heuristic: a real meta-report message has multiple lines that are
    both record-shaped AND identifiably a known player's known deck - not
    just any line that happens to contain a "W-L" pattern (e.g. a
    round-by-round trophy post like "2-0 vs UB Terror" has no player name
    on the line at all). Falls back to a plain record-count check when
    either registry is still empty (e.g. the very first sync ever, with
    nothing yet to cross-check against).
    """
    lines = message.splitlines()
    if not name_registry.entries or not deck_registry.entries:
        return sum(1 for line in lines if RECORD_RE.search(line)) >= min_results
    delimiters = _report_delimiters(message)
    return (
        sum(1 for line in lines if _line_has_known_name_and_deck(line, delimiters, name_registry, deck_registry))
        >= min_results
    )


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


def _line_fields(line: str, delimiters: str) -> tuple[list[str], re.Match] | None:
    """Find the record anywhere in the line - not just where it's already
    isolated by punctuation - then split the text on either side of it on
    whichever of this report's delimiter characters are active. This is
    what makes a line like "chad 3-0 snacker gates" work even though
    there's no delimiter at all between the record and the fields around
    it: the record's own position becomes a delimiter too, splitting
    "before" from "after" regardless of whether real punctuation is there
    to do it.

    A field made of pure punctuation (e.g. a leading "*" bullet marker that
    isn't one of the recognized delimiters) is dropped rather than kept as
    a spurious field - a real name/deck field always has at least one
    letter or digit in it.

    Returns (other_fields, record_match), or None if the line has no
    record at all.
    """
    match = RECORD_RE.search(line)
    if match is None:
        return None
    pattern = _delimiter_regex(delimiters)
    before = pattern.split(line[: match.start()])
    after = pattern.split(line[match.end() :])
    fields = [f.strip() for f in before + after if f.strip() and any(c.isalnum() for c in f)]
    return fields, match


def _split_name_deck(fields: list[str], swapped: bool) -> tuple[str, str]:
    """The player is always the *same end* of a line's remaining fields for
    a given report - first field if not swapped, last if swapped. Anything
    left in between is folded into the deck (merged with a space): a deck's
    own incidental punctuation - e.g. "Mono-White Heroic" splitting into
    "Mono" and "White Heroic" because this report happens to use "-" as its
    own field separator - just means the deck ends up as more than one
    field here, not that the line fails to parse. The merged text doesn't
    need to be an exact match; deck_registry.resolve() fuzzy-matches it (or
    queues it for review) the same as any other raw deck text.
    """
    if len(fields) < 2:
        return "", ""
    if swapped:
        return fields[-1], " ".join(fields[:-1])
    return fields[0], " ".join(fields[1:])


def _order_evidence(fields: list[str], name_registry: NameRegistry, deck_registry: DeckRegistry) -> tuple[int, int]:
    """How much registry evidence supports the player being at the *start*
    of `fields` vs the *end*, with everything else merged into the deck on
    the other side.

    Uses lookup() (thresholded), not raw fuzzy scores: an unrelated
    brand-new name/deck pair still gets *some* nonzero fuzzy score against
    everything in a non-empty registry, so comparing raw scores would flip
    on pure noise whenever both sides are new. Only a clean match counts.
    """
    if len(fields) < 2:
        return 0, 0
    start_name, start_evidence = fields[0], " ".join(fields[1:])
    end_name, end_evidence = fields[-1], " ".join(fields[:-1])
    default_evidence = int(name_registry.lookup(start_name) is not None) + int(
        deck_registry.lookup(start_evidence) is not None
    )
    swapped_evidence = int(name_registry.lookup(end_name) is not None) + int(
        deck_registry.lookup(end_evidence) is not None
    )
    return default_evidence, swapped_evidence


def _report_order_is_swapped(
    message: str, delimiters: str, name_registry: NameRegistry, deck_registry: DeckRegistry
) -> bool:
    """A meta report puts the player at the same end of every line, so
    evidence gathered from whichever lines give a clear registry signal
    settles it for the whole report - including lines whose own name and
    deck are both brand new and would otherwise have nothing to go on.
    """
    total_default = total_swapped = 0
    for line in message.splitlines():
        result = _line_fields(line, delimiters)
        if result is None:
            continue
        fields, _ = result
        default_evidence, swapped_evidence = _order_evidence(fields, name_registry, deck_registry)
        total_default += default_evidence
        total_swapped += swapped_evidence
    return total_swapped > total_default


def _line_has_known_name_and_deck(
    line: str, delimiters: str, name_registry: NameRegistry, deck_registry: DeckRegistry
) -> bool:
    """Whether this line's fields turn up a recognizable player AND a
    recognizable deck, with the player at either end. Used to tell a
    genuine meta-report result apart from a line that's merely
    record-shaped - see is_meta_report.
    """
    result = _line_fields(line, delimiters)
    if result is None:
        return False
    fields, _ = result
    for swapped in (False, True):
        raw_name, raw_deck = _split_name_deck(fields, swapped)
        if raw_name and raw_deck and name_registry.lookup(raw_name) and deck_registry.lookup(raw_deck):
            return True
    return False


def parse_result_line(
    line: str,
    date: date_,
    event: str,
    name_registry: NameRegistry,
    deck_registry: DeckRegistry,
    name_ask: AskCallback | None = None,
    deck_ask: AskCallback | None = None,
    delimiters: str = _CANDIDATE_DELIMITERS,
    swapped: bool = False,
) -> Result | None:
    line = line.strip()
    if not line or line == "...":
        return None

    result = _line_fields(line, delimiters)
    if result is None:
        return None
    fields, match = result
    record = Record.from_match(match)

    raw_name, raw_deck = _split_name_deck(fields, swapped)
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
    delimiters = _report_delimiters(message)
    swapped = _report_order_is_swapped(message, delimiters, name_registry, deck_registry)
    for line in message.splitlines():
        result = parse_result_line(
            line,
            date,
            event,
            name_registry,
            deck_registry,
            name_ask=name_ask,
            deck_ask=deck_ask,
            delimiters=delimiters,
            swapped=swapped,
        )
        if result is not None:
            report.add(result)
    return report
