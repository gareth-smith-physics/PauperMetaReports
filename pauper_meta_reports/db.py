from __future__ import annotations

import os
from urllib.parse import quote_plus

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.collection import Collection

load_dotenv()

_DB_NAME = os.getenv("MONGODB_DB_NAME", "pauper_meta_reports")
_client: MongoClient | None = None


def _build_uri() -> str:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI not set - copy .env.example to .env and fill it in.")
    # Supports both a fully-formed URI (credentials already embedded) and the
    # Atlas "Connect Your Application" template, which has literal <username>/
    # <password> placeholders to fill in from separate env vars. If the
    # placeholders aren't present this is a no-op.
    username = os.getenv("MONGODB_USERNAME")
    password = os.getenv("MONGODB_PASSWORD")
    if username:
        uri = uri.replace("<username>", quote_plus(username))
    if password:
        uri = uri.replace("<password>", quote_plus(password))
    return uri


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(_build_uri(), tlsCAFile=certifi.where())
    return _client


def get_collection(name: str) -> Collection:
    return get_client()[_DB_NAME][name]
