from __future__ import annotations

from datetime import date as date_

from .db import get_collection
from .registry import AskCallback, normalize

# Below this score, the closest registry entry is unrelated enough that
# comparing against it ("Is Penelope the same as Nolan?") would be
# nonsensical, so a plain "this is new" question is shown instead.
_MIN_SCORE_TO_SHOW_CANDIDATE = 70.0


def ask_terminal(kind: str, allow_skip: bool = True) -> AskCallback:
    """Build an AliasRegistry `ask` callback that prompts on the terminal.

    `kind` names the entity being disambiguated (e.g. "player" or "deck") and
    is slotted into the question text. Fires for anything short of an exact
    match, so every new or ambiguous name/deck gets a human's say-so.

    `allow_skip` must match the registry's own `allow_skip` setting - it only
    controls what the prompt says Enter does, not what actually happens.
    """

    def ask(raw: str, candidate: str | None, score: float, matched_alias: str | None) -> str:
        if candidate is None or score < _MIN_SCORE_TO_SHOW_CANDIDATE:
            print(f"\n**{raw}** doesn't match any {kind} in the registry.")
            string = f"  [n]ew {kind} / [Enter] to skip (leave unknown) / or type the correct {kind} name: " if allow_skip else f"  [Enter] for new {kind} / or type the correct {kind} name: "
            return input(string)

        via_note = ""
        if matched_alias and normalize(matched_alias) != normalize(candidate):
            via_note = f' (matched via "{matched_alias}")'
        print(f"\nIs **{raw}** the same as **{candidate}**{via_note}? Or a new {kind}, or a different {kind}?")
        string = f"  [y]es, same / [n]o, new {kind} / [Enter] to skip (leave unknown) / or type the correct {kind} name: " if allow_skip else f"  [y]es, same / [n]o, new {kind} / or type the correct {kind} name: "
        return input(string)

    return ask


def ask_queue_for_review(registry_name: str, date: date_, event: str) -> AskCallback:
    """Build an AliasRegistry `ask` callback for headless runs with no human
    present (e.g. the scheduled Discord sync): instead of blocking on a
    terminal prompt, park the ambiguous match in MongoDB's `unresolved`
    collection and defer (blank answer -> resolve() returns None, matching
    the same "leave unresolved" semantics ask_terminal's Enter key has).

    `registry_name` is "names" or "decks" - whichever registry this ask is
    for - so the review UI knows which registry to apply the answer to.
    `date`/`event` are the report this ambiguity came from, for context.
    """

    def ask(raw: str, candidate: str | None, score: float, matched_alias: str | None) -> str:
        collection = get_collection("unresolved")
        # Keyed on (registry, raw), not auto _id: the same ambiguous text can
        # recur across many reports before anyone reviews it, and each
        # recurrence should update the one queue entry (to the latest
        # date/event it showed up in) rather than pile up duplicates.
        collection.update_one(
            {"registry": registry_name, "raw": raw},
            {
                "$set": {
                    "candidate": candidate,
                    "score": score,
                    "matched_alias": matched_alias,
                    "date": date.isoformat(),
                    "event": event,
                }
            },
            upsert=True,
        )
        return ""

    return ask


def queue_lgs_review(date: date_, default_event: str, message: str) -> None:
    """Flag a meta-report message whose venue couldn't be determined from its
    text - it got recorded under `default_event` as a placeholder so the
    results aren't lost or blocked. Distinct shape from ask_queue_for_review:
    there's no fuzzy candidate to confirm here (nothing in the text matched
    any known LGS at all), so the review UI needs the message text itself,
    not a candidate/score, for a human to judge which venue it really was.

    Keyed on date alone (not date+event, since the real event is exactly
    what's unknown) - a second still-unresolved message on the same date
    updates this one entry rather than piling up duplicates.
    """
    collection = get_collection("unresolved")
    collection.update_one(
        {"registry": "lgs", "raw": date.isoformat()},
        {
            "$set": {
                "date": date.isoformat(),
                "event": default_event,
                "message_snippet": message.strip()[:300],
            }
        },
        upsert=True,
    )
