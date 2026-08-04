from __future__ import annotations

from .registry import AskCallback

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

    def ask(raw: str, candidate: str | None, score: float) -> str:
        if candidate is None or score < _MIN_SCORE_TO_SHOW_CANDIDATE:
            print(f"\n**{raw}** doesn't match any {kind} in the registry.")
            string = f"  [n]ew {kind} / [Enter] to skip (leave unknown) / or type the correct {kind} name: " if allow_skip else f"  [Enter] for new {kind} / or type the correct {kind} name: "
            return input(string)

        print(f"\nIs **{raw}** the same as **{candidate}**? Or a new {kind}, or a different {kind}?")
        string = f"  [y]es, same / [n]o, new {kind} / [Enter] to skip (leave unknown) / or type the correct {kind} name: " if allow_skip else f"  [y]es, same / [n]o, new {kind} / or type the correct {kind} name: "
        return input(string)

    return ask
