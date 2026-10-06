"""PostgreSQL implementation of the Store contract (psycopg 3 + connection pool). JSONB for payloads; per-run event sequence is allocated
atomically with UPDATE … RETURNING so concurrent writers can never produce duplicate or gapped sequence numbers."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .migrate import migrate
from .store import ACTIVE, RUN_FIELDS, Store, StoreError

_RUN_COLS = "id, objective, status, stop_reason, final_answer, state_json, evaluation, tokens, max_steps, request_id, created_at, finished_at"


def _iso(v):
    return v.isoformat(timespec="milliseconds") if v is not None else None


def _run(row):
    if row is None:
        return None
    row = dict(row)
    row["created_at"], row["finished_at"] = _iso(row["created_at"]), _iso(row["finished_at"])
    if isinstance(row.get("state_json"), (dict, list)):
        row["state_json"] = json.dumps(row["state_json"])
    return row


class PostgresStore(Store):
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 8):
        self.dsn = dsn
        self.pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, kwargs={"row_factory": dict_row, "autocommit": True}, open=False)

    def init(self):
        self.pool.open(wait=True, timeout=15)
        with psycopg.connect(self.dsn, autocommit=True) as c:      # plain tuple rows for the migrator
            migrate(c)

    def ping(self):
        try:
            with self.pool.connection(timeout=2) as c:
                c.execute("SELECT 1")
            return True
        except Exception:                                  # noqa: BLE001
            return False

    def close(self):
        self.pool.close()

    def create_run(self, objective, max_steps, request_id=""):
        rid = uuid.uuid4().hex[:16]
        with self.pool.connection() as c:
            row = c.execute(f"INSERT INTO runs (id, objective, status, max_steps, request_id) VALUES (%s,%s,'queued',%s,%s) RETURNING {_RUN_COLS}",
                            (rid, objective, max_steps, request_id)).fetchone()
        return _run(row)

    def update_run(self, run_id, **fields):
        bad = set(fields) - RUN_FIELDS
        if bad:
            raise StoreError(f"unknown run fields {sorted(bad)}")
        if not fields:
            return
        sets, vals = [], []
        for k, v in fields.items():
            if k == "state_json" and isinstance(v, str):
                v = Jsonb(json.loads(v))
            elif k == "evaluation" and v is not None:
                v = Jsonb(v)
            sets.append(f"{k} = %s")
            vals.append(v)
        with self.pool.connection() as c:
            c.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id = %s", (*vals, run_id))

    def get_run(self, run_id):
        with self.pool.connection() as c:
            return _run(c.execute(f"SELECT {_RUN_COLS} FROM runs WHERE id = %s", (run_id,)).fetchone())

    def list_runs(self, limit=50, offset=0):
        with self.pool.connection() as c:
            return [_run(r) for r in c.execute(f"SELECT {_RUN_COLS} FROM runs ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset)).fetchall()]

    def append_event(self, run_id, type_, payload):
        with self.pool.connection() as c, c.transaction():
            row = c.execute("UPDATE runs SET event_seq = event_seq + 1 WHERE id = %s RETURNING event_seq", (run_id,)).fetchone()
            if row is None:
                raise StoreError(f"unknown run {run_id}")
            c.execute("INSERT INTO run_events (run_id, seq, type, payload) VALUES (%s,%s,%s,%s)", (run_id, row["event_seq"], type_, Jsonb(payload)))
            return row["event_seq"]

    def events(self, run_id, after=0, limit=500):
        with self.pool.connection() as c:
            rows = c.execute("SELECT seq, type, payload, ts FROM run_events WHERE run_id = %s AND seq > %s ORDER BY seq LIMIT %s", (run_id, after, limit)).fetchall()
        return [{"seq": r["seq"], "type": r["type"], "payload": r["payload"], "ts": _iso(r["ts"])} for r in rows]

    def create_approval(self, run_id, tool, args):
        aid = uuid.uuid4().hex[:16]
        with self.pool.connection() as c:
            c.execute("INSERT INTO approvals (id, run_id, tool, args) VALUES (%s,%s,%s,%s)", (aid, run_id, tool, Jsonb(args)))
        return aid

    @staticmethod
    def _appr(r):
        return None if r is None else {**r, "created_at": _iso(r["created_at"]), "decided_at": _iso(r["decided_at"])}

    def get_approval(self, approval_id):
        with self.pool.connection() as c:
            return self._appr(c.execute("SELECT * FROM approvals WHERE id = %s", (approval_id,)).fetchone())

    def decide_approval(self, approval_id, approved):
        with self.pool.connection() as c:
            cur = c.execute("UPDATE approvals SET status = %s, decided_at = now() WHERE id = %s AND status = 'pending'", ("approved" if approved else "denied", approval_id))
            return cur.rowcount == 1

    def list_approvals(self, run_id):
        with self.pool.connection() as c:
            return [self._appr(r) for r in c.execute("SELECT * FROM approvals WHERE run_id = %s ORDER BY created_at", (run_id,)).fetchall()]

    def save_eval(self, aggregate, rows):
        eid = uuid.uuid4().hex[:16]
        with self.pool.connection() as c:
            c.execute("INSERT INTO eval_runs (id, aggregate, rows) VALUES (%s,%s,%s)", (eid, Jsonb(aggregate), Jsonb(rows)))
        return eid

    def list_evals(self, limit=20):
        with self.pool.connection() as c:
            rows = c.execute("SELECT id, created_at, aggregate, rows FROM eval_runs ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
        return [{**r, "created_at": _iso(r["created_at"])} for r in rows]

    def mark_interrupted(self):
        with self.pool.connection() as c:
            cur = c.execute("UPDATE runs SET status = 'failed', stop_reason = 'interrupted', finished_at = now() WHERE status = ANY(%s)", (list(ACTIVE),))
            return cur.rowcount
