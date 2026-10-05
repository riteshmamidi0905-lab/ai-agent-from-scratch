"""V3 — explicit state.

"Where does state live?" In ONE serialisable object the loop owns — not inside the model, not in globals.
The model is stateless: every call receives the whole relevant history again. Everything the runtime knows about a run
(status, steps, tool results, errors, tokens) is in RunState, so a run can be inspected, saved, replayed and evaluated.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .model import Usage

STATUSES = ("running", "completed", "failed", "escalated", "stopped")


@dataclass
class StepRecord:
    index: int
    kind: str                    # 'model' | 'tool' | 'plan'
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunState:
    objective: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "running"
    stop_reason: str = ""
    final_answer: Optional[str] = None
    steps: List[StepRecord] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)      # tool_call_id -> result (also the idempotency cache)
    usage: Usage = field(default_factory=Usage)
    scratch: Dict[str, Any] = field(default_factory=dict)                      # task-level working state

    def record(self, kind: str, **detail: Any) -> StepRecord:
        s = StepRecord(len(self.steps), kind, detail)
        self.steps.append(s)
        return s

    def fail(self, kind: str, message: str) -> None:
        self.errors.append({"kind": kind, "message": message, "step": len(self.steps)})

    def finish(self, status: str, reason: str, answer: Optional[str] = None) -> None:
        assert status in STATUSES
        self.status, self.stop_reason, self.final_answer = status, reason, answer

    def to_json(self) -> str:
        d = asdict(self)
        d["usage"]["total"] = self.usage.total
        return json.dumps(d, default=str, indent=2)

    @staticmethod
    def from_json(s: str) -> "RunState":
        d = json.loads(s)
        d["usage"].pop("total", None)
        d["usage"] = Usage(**d["usage"])
        d["steps"] = [StepRecord(**x) for x in d["steps"]]
        return RunState(**d)
