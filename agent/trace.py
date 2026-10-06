"""V9 — observability.

You cannot debug or evaluate what you cannot see. The tracer records STRUCTURED EVENTS (not log strings): every model call,
tool call, retry, approval and failure is a dict with a run id, a monotonic timestamp, a duration and a failure category.
Events never contain hidden reasoning — only what actually crossed a boundary (inputs/outputs of calls, decisions the runtime made).
"""
from __future__ import annotations

import json
import time

from .security import redact
from collections import Counter
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

# every failure in the runtime is classified into exactly one of these
FAILURE_KINDS = ("provider", "network", "malformed", "structured", "tool_error", "tool_timeout", "validation", "denied",
                 "injection_blocked", "loop_detected", "step_limit", "token_budget", "escalated")


def _clean(v):
    """Secrets never reach a trace: redact every string in an event, recursively."""
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


class Tracer:
    def __init__(self, run_id: str = "run", sink: Optional[str] = None, clock=time.monotonic):
        self.run_id, self.sink, self.clock = run_id, sink, clock
        self.events: List[Dict[str, Any]] = []
        self._t0 = clock()

    def emit(self, type_: str, **fields: Any) -> Dict[str, Any]:
        ev = _clean({"run": self.run_id, "t": round(self.clock() - self._t0, 6), "type": type_, **fields})
        self.events.append(ev)
        if self.sink:
            with open(self.sink, "a") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        return ev

    @contextmanager
    def span(self, type_: str, **fields: Any):
        """Time a block; emits one event on exit with duration_ms and ok/failure."""
        start = self.clock()
        info: Dict[str, Any] = {}
        try:
            yield info
        except Exception as e:                       # noqa: BLE001 - recorded then re-raised
            self.emit(type_, duration_ms=round((self.clock() - start) * 1000, 3), ok=False, error=str(e)[:200], **fields, **info)
            raise
        else:
            self.emit(type_, duration_ms=round((self.clock() - start) * 1000, 3), ok=True, **fields, **info)

    def failure(self, kind: str, **fields: Any) -> None:
        assert kind in FAILURE_KINDS, kind
        self.emit("failure", kind=kind, **fields)

    def summary(self) -> Dict[str, Any]:
        model = [e for e in self.events if e["type"] == "model_call"]
        tools = [e for e in self.events if e["type"] == "tool_call"]
        fails = Counter(e["kind"] for e in self.events if e["type"] == "failure")
        return {
            "model_calls": len(model), "tool_calls": len(tools),
            "model_ms": round(sum(e.get("duration_ms", 0) for e in model), 3),
            "tool_ms": round(sum(e.get("duration_ms", 0) for e in tools), 3),
            "prompt_tokens": sum(e.get("prompt_tokens", 0) for e in model), "completion_tokens": sum(e.get("completion_tokens", 0) for e in model),
            "failures": dict(fails),
        }
