from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from pymongo.collection import Collection
from rapidfuzz import fuzz, process

from .db import get_collection

# Signature for the interactive confirmation hook passed to resolve():
#   ask(raw_text, candidate_canonical_or_None, score, matched_alias_or_None) -> answer
# matched_alias is the specific alias/canonical string that produced the
# match, which can differ from the candidate itself. Answer is "y"/"yes" to
# accept the candidate, "n"/"no"/"" for a brand-new entry, or any other text
# naming the actual correct canonical.
AskCallback = Callable[[str, str | None, float, str | None], str]

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
    # Stable identity for the Mongo document, independent of `canonical` -
    # canonical can change (e.g. "Nolan" -> "Nolan S"), the underlying
    # document must not, or a rename would leave an orphaned duplicate.
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def all_names(self) -> list[str]:
        return [self.canonical, *self.aliases]


class AliasRegistry:
    """Canonical-entry + alias lookup, backed by a MongoDB collection, with fuzzy fallback.

    Two read paths are exposed on purpose:
      - lookup(): read-only, used when disambiguating text (e.g. splitting a
        line into name/deck) without mutating the registry.
      - resolve(): read + write, used once a raw name/deck is final. Adds a
        new canonical entry (or a new alias on an existing one) when needed.
    """

    def __init__(
        self,
        collection: Collection,
        fuzzy_threshold: float = 88.0,
        scorer=fuzz.WRatio,
        allow_skip: bool = True,
    ):
        self.collection = collection
        self.fuzzy_threshold = fuzzy_threshold
        self.scorer = scorer
        # Whether a blank/Enter answer to `ask` means "skip, leave unresolved"
        # (True, returns None) or "no match, just add as new" (False). Off for
        # players - every result needs a player, so Enter shouldn't produce one
        # with no owner.
        self.allow_skip = allow_skip
        self.entries: list[AliasEntry] = self._load()

    def _load(self) -> list[AliasEntry]:
        return [
            AliasEntry(
                d["canonical"],
                list(d.get("aliases", [])),
                first=d.get("first", ""),
                last=d.get("last", ""),
                entry_id=d["_id"],
            )
            for d in self.collection.find({})
        ]

    def save(self) -> None:
        """Upsert every entry by its stable entry_id. Called after every
        mutation - at this scale (dozens of entries) rewriting all of them is
        cheap, and per-document upserts mean there's never a window where a
        crash mid-save could lose data, unlike a delete-then-reinsert."""
        for entry in self.entries:
            self.collection.replace_one(
                {"_id": entry.entry_id},
                {
                    "_id": entry.entry_id,
                    "canonical": entry.canonical,
                    "aliases": entry.aliases,
                    "first": entry.first,
                    "last": entry.last,
                },
                upsert=True,
            )

    def _candidates(self) -> dict[str, tuple[str, str]]:
        """normalized name/alias -> (canonical, original-cased name/alias)."""
        lookup: dict[str, tuple[str, str]] = {}
        for entry in self.entries:
            for name in entry.all_names():
                lookup[normalize(name)] = (entry.canonical, name)
        return lookup

    def _best_match(self, raw: str) -> tuple[str, float, str] | None:
        """Best fuzzy match regardless of threshold, or None if the registry is empty.

        Returns (canonical, score, matched_text) - matched_text is whichever
        specific alias or canonical string in the registry actually produced
        the match, which can differ from canonical itself (e.g. a deck's
        canonical is "Mono-White Heroic" but the raw text matched via its
        alias "White Weenies").
        """
        normalized = normalize(raw)
        if not normalized:
            return None
        candidates = self._candidates()
        if normalized in candidates:
            canonical, original = candidates[normalized]
            return canonical, 100.0, original
        if not candidates:
            return None
        match = process.extractOne(normalized, candidates.keys(), scorer=self.scorer)
        if match is None:
            return None
        matched_key, score, _ = match
        canonical, original = candidates[matched_key]
        return canonical, score, original

    def _exact_match(self, raw: str) -> str | None:
        normalized = normalize(raw)
        if not normalized:
            return None
        entry = self._candidates().get(normalized)
        return entry[0] if entry else None

    def lookup(self, raw: str) -> tuple[str, float] | None:
        """Read-only match against canonical names/aliases. Returns (canonical, score) or None."""
        best = self._best_match(raw)
        if best is not None and best[1] >= self.fuzzy_threshold:
            return best[0], best[1]
        return None

    def resolve(self, raw: str, auto_add: bool = True, ask: AskCallback | None = None) -> str | None:
        """Resolve raw text to a canonical name, or None if a human skipped it.

        Only an exact match (after normalization) is accepted silently. Anything
        short of that is not a "clean" match, no matter how close the fuzzy
        score is, so:
          - if `ask` is given, a human is always consulted via
            `ask(raw, candidate, score, matched_alias)` before merging or
            adding anything - `candidate`/`score` describe the closest
            existing entry, `matched_alias` is the specific alias/canonical
            string that produced that match (may differ from `candidate`
            itself), or (None, 0.0, None) if the registry has nothing close
            at all. The answer must be "y"/"yes" to accept the candidate,
            "n"/"no"/"new" for a brand-new entry, or any other text naming
            the actual correct canonical (matched against the registry, or
            created if new). A blank/Enter answer means "skip, leave
            unresolved" (returns None, registry untouched, asked again next
            time) when allow_skip is True, otherwise it's treated the same
            as "new".
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
            candidate, score, matched_alias = best if best is not None else (None, 0.0, None)
            answer = ask(raw, candidate, score, matched_alias).strip()

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
                candidate, score, _ = best
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

    def get_entry(self, canonical: str) -> AliasEntry | None:
        """Look up an entry by its exact current canonical name."""
        for entry in self.entries:
            if entry.canonical == canonical:
                return entry
        return None

    def find_conflict(self, entry: AliasEntry, name: str) -> AliasEntry | None:
        """Check whether `name` already belongs to some *other* entry (by
        canonical or alias, normalized) - used before a rename/alias-add to
        avoid silently colliding two distinct entries."""
        target = normalize(name)
        for other in self.entries:
            if other is entry:
                continue
            if any(normalize(n) == target for n in other.all_names()):
                return other
        return None

    def rename_canonical(self, old_canonical: str, new_canonical: str) -> bool:
        """Change an entry's canonical display name in place. The Mongo
        document identity (entry_id) is untouched, so this is safe even
        though `canonical` doubles as this registry's natural lookup key
        elsewhere - the same trick NameRegistry already relies on to rename
        "Nolan" -> "Nolan S" when a second Nolan shows up.

        Doesn't touch anything outside this registry - callers that also
        need past results updated (e.g. History.rename_deck) must do that
        separately.
        """
        new_canonical = new_canonical.strip()
        entry = self.get_entry(old_canonical)
        if entry is None or not new_canonical:
            return False
        entry.canonical = new_canonical
        self.save()
        return True

    def add_alias(self, canonical: str, alias: str) -> bool:
        """Add an alias to an existing entry. Returns False (no-op) if the
        entry doesn't exist, the alias is blank, it's already there, or it's
        indistinguishable from the canonical name itself."""
        alias = alias.strip()
        entry = self.get_entry(canonical)
        if entry is None or not alias or normalize(alias) == normalize(canonical):
            return False
        if alias in entry.aliases:
            return False
        entry.aliases.append(alias)
        self.save()
        return True

    def remove_alias(self, canonical: str, alias: str) -> bool:
        """Remove an alias from an existing entry. Returns False (no-op) if
        the entry or alias doesn't exist."""
        entry = self.get_entry(canonical)
        if entry is None or alias not in entry.aliases:
            return False
        entry.aliases.remove(alias)
        self.save()
        return True

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
    def __init__(self, collection: Collection | None = None):
        # Plain (non-partial) ratio: WRatio's partial-match behavior treats
        # "John G" as a near-perfect match for "John", which silently merges
        # different people who share a first name. fuzzy_threshold only
        # matters when resolve() is called without a human to ask (ask=None);
        # whenever a human is present, anything short of an exact match asks.
        # allow_skip=True: an unresolved player can be left as None and queued
        # for review (see ask_queue_for_review) rather than forced to guess a
        # new person immediately - the review-queue workflow assumes this.
        super().__init__(
            collection if collection is not None else get_collection("names"),
            fuzzy_threshold=92.0,
            scorer=fuzz.ratio,
            allow_skip=True,
        )

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
    def __init__(self, collection: Collection | None = None):
        # token_sort_ratio so word order doesn't matter ("red madness" vs "madness red").
        super().__init__(
            collection if collection is not None else get_collection("decks"),
            fuzzy_threshold=82.0,
            scorer=fuzz.token_sort_ratio,
        )


class LGSRegistry(AliasRegistry):
    def __init__(self, collection: Collection | None = None):
        # Store names are proper nouns - two different LGSs getting merged would
        # corrupt event-level stats - so use the same conservative plain-ratio
        # scorer as NameRegistry rather than WRatio's eager partial matching.
        super().__init__(
            collection if collection is not None else get_collection("lgs"),
            fuzzy_threshold=90.0,
            scorer=fuzz.ratio,
        )
