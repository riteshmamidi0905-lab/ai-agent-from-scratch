"""Minimal forward-only SQL migrator. Applies service/migrations/NNN_name.sql in order, each in its own transaction, recording
versions in schema_migrations. A pg advisory lock makes concurrent starts (several replicas) safe: one applies, the rest wait.
Chosen over Alembic because the schema is small and a 40-line, readable migrator has no extra dependency."""
from __future__ import annotations

import os
import re
from typing import List

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")
_LOCK_KEY = 7243905  # arbitrary constant shared by all replicas


def migration_files() -> List[str]:
    fs = sorted(f for f in os.listdir(MIGRATIONS_DIR) if re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", f))
    return fs


def migrate(conn) -> List[str]:
    """`conn` is a psycopg connection. Returns the list of versions applied by this call."""
    applied: List[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for f in migration_files():
            if f in done:
                continue
            with open(os.path.join(MIGRATIONS_DIR, f)) as fh:
                conn.execute(fh.read())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f,))
            applied.append(f)
    return applied
