from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from .interactive import ask_queue_for_review, queue_lgs_review
from .models import History
from .parser import find_lgs_in_message, is_meta_report, parse_meta_report, parse_new_lgs_announcement
from .registry import DeckRegistry, LGSRegistry, NameRegistry

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "settings.json"


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        raise FileNotFoundError(
            f"{SETTINGS_PATH} not found - copy settings.example.json to settings.json and fill it in."
        )
    return json.loads(SETTINGS_PATH.read_text())


def run_sync(default_lgs: str | None = None) -> None:
    """Connect to Discord, pull any meta-report messages posted since the last
    sync out of the configured channel, parse them, and disconnect.

    Runs headless: there's no terminal to prompt, so a confident fuzzy match
    on a name or deck is still accepted automatically, but anything looser
    is queued in MongoDB's `unresolved` collection for a human to resolve
    later via the Streamlit review tab, instead of guessing and creating a
    new registry entry outright. Same idea for a message whose venue can't be
    determined at all: it's recorded under `default_lgs` as a placeholder so
    the results aren't lost, and separately queued for someone to confirm or
    correct the real venue.

    `default_lgs`, if given, overrides settings.json's DEFAULT_LGS - useful
    for the very first local run, before any LGS has been registered and
    every message would otherwise need a venue guess.
    """
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN not set - copy .env.example to .env and fill it in.")

    settings = load_settings()
    channel_id = settings["CHANNEL_ID"]
    # Used only when a meta-report message doesn't mention any known LGS by
    # name - e.g. before the first "New LGS: ..." announcement has ever been
    # posted. Once the registry has entries, most messages should resolve to
    # a real LGS via find_lgs_in_message() instead of falling back to this.
    default_lgs = default_lgs or settings.get("DEFAULT_LGS")
    if not default_lgs:
        raise RuntimeError(
            "No default LGS available - pass --default-lgs, or set DEFAULT_LGS in settings.json."
        )
    start_date = datetime.fromisoformat(settings["START_DATE"]).replace(tzinfo=timezone.utc)

    name_registry = NameRegistry()
    deck_registry = DeckRegistry()
    lgs_registry = LGSRegistry()
    history = History.load()

    # Re-scan from the last known report's own date (not the day after) so a
    # second same-day report isn't missed; History.has_report() skips anything
    # already recorded so this is safe to overlap.
    after = (
        datetime.combine(history.last_date, datetime.min.time(), tzinfo=timezone.utc)
        if history.last_date is not None
        else start_date
    )

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!unused-meta-reports-sync", intents=intents)

    @bot.event
    async def on_ready() -> None:
        try:
            print(f"Logged in as {bot.user}")
            channel = bot.get_channel(channel_id)
            if channel is None:
                print(f"Channel {channel_id} not found, or the bot can't see it.")
                return

            print(f"Scanning #{channel} for meta reports posted after {after.date()}...")
            new_reports = 0
            async for message in channel.history(after=after, oldest_first=True):
                new_lgs = parse_new_lgs_announcement(message.content)
                if new_lgs is not None:
                    if lgs_registry.add_canonical(new_lgs):
                        print(f"  Registered new LGS: {new_lgs}")
                    # Not `continue`-ing here on purpose: a message can announce a
                    # new LGS *and* contain that week's results in the same post.

                if not is_meta_report(message.content):
                    continue

                report_date = message.created_at.date()
                found_event = find_lgs_in_message(message.content, lgs_registry)
                event = found_event or default_lgs
                if found_event is None:
                    queue_lgs_review(report_date, default_lgs, message.content)

                if history.has_report(report_date, event):
                    continue

                report = parse_meta_report(
                    message.content,
                    date=report_date,
                    event=event,
                    name_registry=name_registry,
                    deck_registry=deck_registry,
                    name_ask=ask_queue_for_review("names", report_date, event),
                    deck_ask=ask_queue_for_review("decks", report_date, event),
                )
                if history.add(report):  # add() persists to MongoDB immediately
                    new_reports += 1
                    print(f"  {report_date}: recorded {len(report)} result(s)")

            print(f"Done. {new_reports} new meta report(s) recorded.")
        finally:
            await bot.close()

    bot.run(token)


if __name__ == "__main__":
    run_sync()
