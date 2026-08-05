from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pauper_meta_reports import LGSRegistry


def main() -> None:
    name = input("LGS name: ").strip()
    if not name:
        print("No name entered - aborting.")
        return

    aliases_input = input("Aliases (comma-separated, optional): ").strip()
    aliases = [a.strip() for a in aliases_input.split(",") if a.strip()] if aliases_input else []

    registry = LGSRegistry()
    added = registry.add_canonical(name, aliases=aliases)

    if added:
        suffix = f" (aliases: {', '.join(aliases)})" if aliases else ""
        print(f"Added new LGS: {name}{suffix}")
    else:
        match = registry.lookup(name)
        print(f"Already known - '{name}' matches existing LGS '{match[0]}' (score {match[1]:.0f}). Nothing added.")


if __name__ == "__main__":
    main()
