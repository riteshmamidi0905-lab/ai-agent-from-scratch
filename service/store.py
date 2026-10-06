"""Persistence contract + in-memory implementation. PostgresStore (pg_store.py) implements the same interface and is checked by the same
contract tests (tests/test_store_contract.py)."""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ACTIVE = ("queued", "running", "awaiting_approval")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class StoreError(Exception):
    pass


class Store:
    """Interface. All methods are synchronous and thread-safe."""

    def init(self) -> None: ...
    def ping(self) -> bool: ...
    def create_run(self, objective: str, max_steps: int, request_id: str = "") -> Dict[str, Any]: ...
    def update_run(self, run_id: str, **fields: Any) -> None: ...
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]: ...
    def list_runs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]: ...
    def append_event(self, run_id: str, type_: str, payload: Dict[str, Any]) -> int: ...
    def events(self, run_id: str, after: int = 0, limit: int = 500) -> List[Dict[str, Any]]: ...
    def create_approval(self, run_id: str, tool: str, args: Dict[str, Any]) -> str: ...
    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]: ...
    def decide_approval(self, approval_id: str, approved: bool) -> bool: ...
    def list_approvals(self, run_id: str) -> List[Dict[str, Any]]: ...
    def save_eval(self, aggregate: Dict[str, Any], rows: List[Dict[str, Any]]) -> str: ...
    def list_evals(self, limit: int = 20) -> List[Dict[str, Any]]: ...
    def mark_interrupted(self) -> int: ...
    def close(self) -> None: ...


RUN_FIELDS = {"status", "stop_reason", "final_answer", "state_json", "evaluation", "tokens", "finished_at"}


class MemoryStore(Store):
    def __init__(self):
        self._lock = threading.RLock()
        self.runs: Dict[str, Dict[str, Any]] = {}
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self.approvals: Dict[str, Dict[str, Any]] = {}
        self._evals: List[Dict[str, Any]] = []

    def init(self): pass
    def ping(self): return True
    def close(self): pass

    def create_run(self, objective, max_steps, request_id=""):
        with self._lock:
            r = {"id": uuid.uuid4().hex[:16], "objective": objective, "status": "queued", "stop_reason": "", "final_answer": None, "state_json": None,
                 "evaluation": None, "tokens": 0, "max_steps": max_steps, "request_id": request_id, "created_at": now(), "finished_at": None}
            self.runs[r["id"]] = r
            self._events[r["id"]] = []
            return copy.deepcopy(r)

    def update_run(self, run_id, **fields):
        bad = set(fields) - RUN_FIELDS
        if bad:
            raise StoreError(f"unknown run fields {sorted(bad)}")
        with self._lock:
            self.runs[run_id].update(copy.deepcopy(fields))

    def get_run(self, run_id):
        with self._lock:
            r = self.runs.get(run_id)
            return copy.deepcopy(r) if r else None

    def list_runs(self, limit=50, offset=0):
        with self._lock:
            rs = list(reversed(list(self.runs.values())))          # dicts keep insertion order: newest first
            return [copy.deepcopy(r) for r in rs[offset:offset + limit]]

    def append_event(self, run_id, type_, payload):
        with self._lock:
            evs = self._events[run_id]
            seq = len(evs) + 1
            evs.append({"seq": seq, "type": type_, "payload": copy.deepcopy(payload), "ts": now()})
            return seq

    def events(self, run_id, after=0, limit=500):
        with self._lock:
            return copy.deepcopy([e for e in self._events.get(run_id, []) if e["seq"] > after][:limit])

    def create_approval(self, run_id, tool, args):
        with self._lock:
            a = {"id": uuid.uuid4().hex[:16], "run_id": run_id, "tool": tool, "args": copy.deepcopy(args), "status": "pending", "created_at": now(), "decided_at": None}
            self.approvals[a["id"]] = a
            return a["id"]

    def get_approval(self, approval_id):
        with self._lock:
            a = self.approvals.get(approval_id)
            return copy.deepcopy(a) if a else None

    def decide_approval(self, approval_id, approved):
        """Atomic: only a *pending* approval can be decided, exactly once. Returns False if already decided/unknown."""
        with self._lock:
            a = self.approvals.get(approval_id)
            if not a or a["status"] != "pending":
                return False
            a["status"], a["decided_at"] = ("approved" if approved else "denied"), now()
            return True

    def list_approvals(self, run_id):
        with self._lock:
            return copy.deepcopy([a for a in self.approvals.values() if a["run_id"] == run_id])

    def save_eval(self, aggregate, rows):
        with self._lock:
            e = {"id": uuid.uuid4().hex[:16], "created_at": now(), "aggregate": copy.deepcopy(aggregate), "rows": copy.deepcopy(rows)}
            self._evals.append(e)
            return e["id"]

    def list_evals(self, limit=20):
        with self._lock:
            return copy.deepcopy(list(reversed(self._evals))[:limit])

    def mark_interrupted(self):
        with self._lock:
            n = 0
            for r in self.runs.values():
                if r["status"] in ACTIVE:
                    r["status"], r["stop_reason"], r["finished_at"] = "failed", "interrupted", now()
                    n += 1
            return n
