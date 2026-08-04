from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

# Signature for the interactive confirmation hook passed to resolve():
#   ask(raw_text, candidate_canonical_or_None, score) -> answer
# where answer is "y"/"yes" to accept the candidate, "n"/"no"/"" for a
# brand-new entry, or any other text naming the actual correct canonical.
AskCallback = Callable[[str, str | None, float], str]

_ASIDE_RE = re.compile(r"\([^)]*\)")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Casefold and strip parenthetical asides for matching purposes."""
    text = _ASIDE_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip().lower()


def clean_for_storage(text: str) -> str:
    """Strip parenthetical asides and title-case each word, for new canonical entries.

    Applied only when the parsing process mints a brand-new canonical entry
    (raw player/deck text is inconsistently cased - "chad", "HUXLEY BERGMAN",
    "monoU terror"); Goldfish-seeded decks and existing entries are untouched.
    """
    text = _ASIDE_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = text.strip()
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split(" ") if word)


@dataclass
class AliasEntry:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    # first/last are only populated by NameRegistry, to support renaming a
    # player's canonical display name if a same-first-name conflict shows up
    # later. Decks leave these blank.
    first: str = ""
    last: str = ""

    def all_names(self) -> list[str]:
        return [self.canonical, *self.aliases]


class AliasRegistry:
    """Canonical-entry + alias lookup, backed by a JSON file, with fuzzy fallback.

    Two read paths are exposed on purpose:
      - lookup(): read-only, used when disambiguating text (e.g. splitting a
        line into name/deck) without mutating the registry.
      - resolve(): read + write, used once a raw name/deck is final. Adds a
        new canonical entry (or a new alias on an existing one) when needed.
    """

    def __init__(
        self,
        path: Path,
        fuzzy_threshold: float = 88.0,
        scorer=fuzz.WRatio,
        allow_skip: bool = True,
    ):
        self.path = Path(path)
        self.fuzzy_threshold = fuzzy_threshold
        self.scorer = scorer
        # Whether a blank/Enter answer to `ask` means "skip, leave unresolved"
        # (True, returns None) or "no match, just add as new" (False). Off for
        # players - every result needs a player, so Enter shouldn't produce one
        # with no owner.
        self.allow_skip = allow_skip
        self.entries: list[AliasEntry] = self._load()

    def _load(self) -> list[AliasEntry]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [
            AliasEntry(
                e["canonical"],
                list(e.get("aliases", [])),
                first=e.get("first", ""),
                last=e.get("last", ""),
            )
            for e in raw
        ]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"canonical": e.canonical, "aliases": e.aliases, "first": e.first, "last": e.last}
            for e in self.entries
        ]
        self.path.write_text(json.dumps(payload, indent=2))

    def _candidates(self) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for entry in self.entries:
            for name in entry.all_names():
                lookup[normalize(name)] = entry.canonical
        return lookup

    def _best_match(self, raw: str) -> tuple[str, float] | None:
        """Best fuzzy match regardless of threshold, or None if the registry is empty."""
        normalized = normalize(raw)
        if not normalized:
            return None
        candidates = self._candidates()
        if normalized in candidates:
            return candidates[normalized], 100.0
        if not candidates:
            return None
        match = process.extractOne(normalized, candidates.keys(), scorer=self.scorer)
        if match is None:
            return None
        matched_text, score, _ = match
        return candidates[matched_text], score

    def _exact_match(self, raw: str) -> str | None:
        normalized = normalize(raw)
        if not normalized:
            return None
        return self._candidates().get(normalized)

    def lookup(self, raw: str) -> tuple[str, float] | None:
        """Read-only match against canonical names/aliases. Returns (canonical, score) or None."""
        best = self._best_match(raw)
        if best is not None and best[1] >= self.fuzzy_threshold:
            return best
        return None

    def resolve(self, raw: str, auto_add: bool = True, ask: AskCallback | None = None) -> str | None:
        """Resolve raw text to a canonical name, or None if a human skipped it.

        Only an exact match (after normalization) is accepted silently. Anything
        short of that is not a "clean" match, no matter how close the fuzzy
        score is, so:
          - if `ask` is given, a human is always consulted via
            `ask(raw, candidate, score)` before merging or adding anything -
            `candidate`/`score` describe the closest existing entry, or
            (None, 0.0) if the registry has nothing close at all. The answer
            must be "y"/"yes" to accept the candidate, "n"/"no"/"new" for a
            brand-new entry, or any other text naming the actual correct
            canonical (matched against the registry, or created if new). A
            blank/Enter answer means "skip, leave unresolved" (returns None,
            registry untouched, asked again next time) when allow_skip is
            True, otherwise it's treated the same as "new".
          - otherwise (no human available), a confident fuzzy match
            (score >= fuzzy_threshold) is accepted automatically and anything
            looser becomes a new canonical entry. There's no one to skip for,
            so this path never returns None.
        """
        exact = self._exact_match(raw)
        if exact is not None:
            return exact

        if ask is not None:
            best = self._best_match(raw)
            candidate, score = best if best is not None else (None, 0.0)
            answer = ask(raw, candidate, score).strip()

            if not answer and self.allow_skip:
                return None

            if candidate is not None and answer.lower() in ("y", "yes"):
                self._add_alias(candidate, raw)
                return candidate
            if answer and answer.lower() not in ("n", "no", "new"):
                canonical = self.resolve(answer, auto_add=auto_add)
                self._add_alias(canonical, raw)
                return canonical
        else:
            best = self._best_match(raw)
            if best is not None and best[1] >= self.fuzzy_threshold:
                candidate, score = best
                self._add_alias(candidate, raw)
                return candidate

        if not auto_add:
            raise KeyError(f"No match for {raw!r}")
        return self._add_new(raw)

    def _add_new(self, raw: str) -> str:
        """Create a brand-new canonical entry for `raw` and persist it.

        Overridden by NameRegistry to apply first/last-name disambiguation.
        """
        canonical = clean_for_storage(raw)
        self.entries.append(AliasEntry(canonical=canonical, aliases=[]))
        self.save()
        return canonical

    def _add_alias(self, canonical: str, alias: str) -> None:
        alias = alias.strip()
        for entry in self.entries:
            if entry.canonical == canonical:
                if alias and alias not in entry.aliases and normalize(alias) != normalize(canonical):
                    entry.aliases.append(alias)
                    self.save()
                return

    def add_canonical(self, canonical: str, aliases: list[str] | None = None) -> bool:
        """Add a brand-new canonical entry if it doesn't already resolve to one.

        Returns True if a new entry was added, False if it already matched something.
        """
        if self.lookup(canonical) is not None:
            return False
        self.entries.append(AliasEntry(canonical=canonical.strip(), aliases=list(aliases or [])))
        self.save()
        return True


class NameRegistry(AliasRegistry):
    def __init__(self, path: Path | str = Path("data/names.json")):
        # Plain (non-partial) ratio: WRatio's partial-match behavior treats
        # "John G" as a near-perfect match for "John", which silently merges
        # different people who share a first name. fuzzy_threshold only
        # matters when resolve() is called without a human to ask (ask=None);
        # whenever a human is present, anything short of an exact match asks.
        # allow_skip=False: every result needs a player, so pressing Enter
        # adds a new person instead of leaving the result ownerless.
        super().__init__(path, fuzzy_threshold=92.0, scorer=fuzz.ratio, allow_skip=False)

    def _add_new(self, raw: str) -> str:
        """Add a new player, then re-derive display names for everyone who
        shares their first name: first name alone if it's unique, first name
        + last initial if that's enough to disambiguate, otherwise the full
        name. This can rename existing entries too, e.g. a lone "Nolan"
        becomes "Nolan S" the moment a second Nolan is added.
        """
        cleaned = clean_for_storage(raw)
        first, _, last = cleaned.partition(" ")
        entry = AliasEntry(canonical=first, aliases=[], first=first, last=last)
        self.entries.append(entry)
        self._rename_first_name_group(first)
        self.save()
        return entry.canonical

    def _rename_first_name_group(self, first: str) -> None:
        first_key = normalize(first)
        group = [e for e in self.entries if normalize(e.first) == first_key]

        def remember_full_name(e: AliasEntry) -> None:
            # Whatever display form wins, keep "First Last" matchable too, so
            # a repeat of the same full raw text resolves without re-asking.
            if e.last:
                full = f"{e.first} {e.last}"
                if full != e.canonical and full not in e.aliases:
                    e.aliases.append(full)

        if len(group) <= 1:
            for e in group:
                e.canonical = e.first
                remember_full_name(e)
            return

        initial_counts: dict[str, int] = {}
        for e in group:
            if e.last:
                key = f"{e.first} {e.last[0].upper()}"
                initial_counts[key] = initial_counts.get(key, 0) + 1

        for e in group:
            if not e.last:
                e.canonical = e.first
                continue
            key = f"{e.first} {e.last[0].upper()}"
            e.canonical = f"{e.first} {e.last}" if initial_counts[key] > 1 else key
            remember_full_name(e)


class DeckRegistry(AliasRegistry):
    def __init__(self, path: Path | str = Path("data/decks.json")):
        # token_sort_ratio so word order doesn't matter ("red madness" vs "madness red").
        super().__init__(path, fuzzy_threshold=82.0, scorer=fuzz.token_sort_ratio)


class LGSRegistry(AliasRegistry):
    def __init__(self, path: Path | str = Path("data/lgs.json")):
        # Store names are proper nouns - two different LGSs getting merged would
        # corrupt event-level stats - so use the same conservative plain-ratio
        # scorer as NameRegistry rather than WRatio's eager partial matching.
        super().__init__(path, fuzzy_threshold=90.0, scorer=fuzz.ratio)
