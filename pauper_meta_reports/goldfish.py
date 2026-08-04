from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from .registry import DeckRegistry

GOLDFISH_URL = "https://www.mtggoldfish.com/metagame/pauper/full#paper"
_USER_AGENT = "Mozilla/5.0 (compatible; PauperMetaReportsBot/0.1)"


def fetch_goldfish_deck_names(url: str = GOLDFISH_URL) -> list[str]:
    """Scrape the paper-Pauper archetype names off the Goldfish metagame page."""
    response = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    names = []
    for title in soup.select("div.archetype-tile-title"):
        link = title.select_one("span.deck-price-paper a")
        if link and link.text.strip():
            names.append(link.text.strip())
    return names


def update_deck_registry_from_goldfish(
    deck_registry: DeckRegistry, url: str = GOLDFISH_URL
) -> list[str]:
    """Add any Goldfish paper-Pauper archetypes missing from the registry.

    Returns the canonical names that were newly added.
    """
    added = []
    for name in fetch_goldfish_deck_names(url):
        if deck_registry.add_canonical(name):
            added.append(name)
    return added
