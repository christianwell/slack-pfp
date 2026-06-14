"""Per-user data model for the multi-tenant Slack PFP service.

Phase 1 first slice: load/save one signed-in Slack user without touching the
rendering pipeline. One SQLite row per user holds their team, encrypted Slack
token, Last.fm username, and per-user config/state JSON (today these still
mirror the single-user shapes from ``core.py``).

Slack tokens are encrypted at rest with Fernet; the key comes from the
``FERNET_KEY`` env var and is never stored in the database or the repo. Generate
one with ``python -m db keygen`` and put it in ``.env``.
"""
import json
import os
import sqlite3
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

import core

DB_PATH = os.path.join(core.BASE_DIR, "users.db")

# Onboarding state machine (see goals.md "Onboarding state machine").
ONBOARDING_STATES = (
    "new",              # row created, nothing connected yet
    "slack_connected",  # Slack OAuth done, token stored
    "lastfm_set",       # Last.fm username validated
    "active",           # updater is driving this user's profile
    "paused",           # user paused updates
    "disconnected",     # token revoked / user disconnected
)

_local = threading.local()


# --------------------------------------------------------------------------- #
# Encryption
# --------------------------------------------------------------------------- #

def _fernet() -> Fernet:
    key = os.getenv("FERNET_KEY", "")
    if not key:
        raise RuntimeError(
            "FERNET_KEY is not set. Generate one with `python -m db keygen` "
            "and add it to your .env (never commit it)."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"FERNET_KEY is invalid: {e}") from e


def encrypt_token(token: str) -> bytes:
    """Encrypt a Slack token for storage. Empty/None -> b'' (no token)."""
    if not token:
        return b""
    return _fernet().encrypt(token.encode())


def decrypt_token(blob: bytes) -> str:
    """Decrypt a stored Slack token. Empty/garbage -> '' (treated as no token)."""
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob).decode()
    except (InvalidToken, ValueError):
        return ""


# --------------------------------------------------------------------------- #
# Connection / schema
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    slack_user_id   TEXT PRIMARY KEY,
    team_id         TEXT NOT NULL,
    slack_token_enc BLOB,
    lastfm_username TEXT,
    config_json     TEXT,
    state_json      TEXT,
    onboarding      TEXT NOT NULL DEFAULT 'new',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
"""


def _connect(path: str | None = None) -> sqlite3.Connection:
    """Return a per-thread SQLite connection with the schema applied."""
    db_path = path or DB_PATH
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == db_path:
        return conn
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _local.conn = conn
    _local.path = db_path
    return conn


def init_db(path: str | None = None) -> None:
    """Create the database/schema if it does not exist."""
    _connect(path).commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# User record
# --------------------------------------------------------------------------- #

def _default_config() -> dict:
    return deepcopy(core.DEFAULT_CONFIG)


@dataclass
class User:
    """One signed-in Slack user and their per-user settings/state."""

    slack_user_id: str
    team_id: str
    slack_token: str = ""               # plaintext in memory, encrypted at rest
    lastfm_username: str = ""
    config: dict = field(default_factory=_default_config)
    state: dict = field(default_factory=dict)
    onboarding: str = "new"
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        cfg = _default_config()
        try:
            stored = json.loads(row["config_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            stored = {}
        # Backfill any missing top-level keys from defaults (matches core.load_config).
        cfg.update(stored)
        for key, val in core.DEFAULT_CONFIG.items():
            cfg.setdefault(key, deepcopy(val))
        try:
            state = json.loads(row["state_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            state = {}
        return cls(
            slack_user_id=row["slack_user_id"],
            team_id=row["team_id"],
            slack_token=decrypt_token(row["slack_token_enc"]),
            lastfm_username=row["lastfm_username"] or "",
            config=cfg,
            state=state,
            onboarding=row["onboarding"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def load_user(slack_user_id: str, path: str | None = None) -> User | None:
    """Load one user by Slack user id, or None if not found."""
    cur = _connect(path).execute(
        "SELECT * FROM users WHERE slack_user_id = ?", (slack_user_id,)
    )
    row = cur.fetchone()
    return User.from_row(row) if row else None


def save_user(user: User, path: str | None = None) -> User:
    """Insert or update a user (upsert on slack_user_id). Returns the user."""
    if user.onboarding not in ONBOARDING_STATES:
        raise ValueError(f"invalid onboarding state: {user.onboarding!r}")
    conn = _connect(path)
    now = _now()
    if not user.created_at:
        user.created_at = now
    user.updated_at = now
    conn.execute(
        """
        INSERT INTO users (
            slack_user_id, team_id, slack_token_enc, lastfm_username,
            config_json, state_json, onboarding, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slack_user_id) DO UPDATE SET
            team_id=excluded.team_id,
            slack_token_enc=excluded.slack_token_enc,
            lastfm_username=excluded.lastfm_username,
            config_json=excluded.config_json,
            state_json=excluded.state_json,
            onboarding=excluded.onboarding,
            updated_at=excluded.updated_at
        """,
        (
            user.slack_user_id,
            user.team_id,
            encrypt_token(user.slack_token),
            user.lastfm_username,
            json.dumps(user.config, ensure_ascii=False),
            json.dumps(user.state, ensure_ascii=False),
            user.onboarding,
            user.created_at,
            user.updated_at,
        ),
    )
    conn.commit()
    return user


def delete_user(slack_user_id: str, path: str | None = None) -> bool:
    """Delete a user and their data. Returns True if a row was removed."""
    conn = _connect(path)
    cur = conn.execute("DELETE FROM users WHERE slack_user_id = ?", (slack_user_id,))
    conn.commit()
    return cur.rowcount > 0


def all_users(path: str | None = None) -> list[User]:
    """Load every user (for the shared worker loop in Phase 2)."""
    cur = _connect(path).execute("SELECT * FROM users ORDER BY created_at")
    return [User.from_row(row) for row in cur.fetchall()]


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "keygen":
        print(Fernet.generate_key().decode())
    else:
        print("usage: python -m db keygen   # print a new FERNET_KEY")
