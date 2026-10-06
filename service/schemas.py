"""Typed request/response models. These are the public contract: internal state (full message text, tool arguments after redaction
beyond what is shown here, raw model content that accompanied tool calls) is deliberately NOT exposed — see to_public_steps."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

RunStatus = str  # queued | running | awaiting_approval | completed | failed | stopped | escalated


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1, description="What the agent should do.")
    max_steps: Optional[int] = Field(default=None, ge=1, le=100, description="Model-call budget; capped by the server.")


class PublicStep(BaseModel):
    index: int
    kind: str
    tool: Optional[str] = None
    ok: Optional[bool] = None
    failure: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    tool_calls: Optional[List[str]] = None
    answer: Optional[str] = None


class RunOut(BaseModel):
    id: str
    status: RunStatus
    objective: str
    stop_reason: str = ""
    final_answer: Optional[str] = None
    evaluation: Optional[Dict[str, Any]] = None
    tokens: int = 0
    max_steps: int
    request_id: str = ""
    created_at: str
    finished_at: Optional[str] = None
    steps: Optional[List[PublicStep]] = None


class EventOut(BaseModel):
    seq: int
    type: str
    ts: str
    payload: Dict[str, Any]


class ApprovalOut(BaseModel):
    id: str
    run_id: str
    tool: str
    args: Dict[str, Any]
    status: str
    created_at: str
    decided_at: Optional[str] = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str = ""


class ErrorOut(BaseModel):
    error: ErrorBody


def to_public_steps(state_json: Optional[str]) -> Optional[List[PublicStep]]:
    """Project the stored run state into what clients may see. Model steps expose ONLY tool names (when it called tools) or the final answer.
    Any free text the model produced alongside tool calls is dropped: it may contain reasoning we do not publish."""
    if not state_json:
        return None
    st = json.loads(state_json)
    out: List[PublicStep] = []
    for s in st.get("steps", []):
        d = s.get("detail", {})
        if s["kind"] == "model":
            calls = [c["name"] for c in d.get("tool_calls", [])]
            out.append(PublicStep(index=s["index"], kind="model", tool_calls=calls or None, answer=None if calls else d.get("content")))
        elif s["kind"] == "tool":
            out.append(PublicStep(index=s["index"], kind="tool", tool=d.get("tool"), ok=d.get("ok"), failure=d.get("failure") or None,
                                  output=(d.get("output") or "")[:300] or None, error=(d.get("error") or "")[:300] or None))
    return out


def run_out(run: Dict[str, Any], with_steps: bool = False) -> RunOut:
    return RunOut(**{k: run[k] for k in ("id", "status", "objective", "stop_reason", "final_answer", "evaluation", "tokens", "max_steps", "request_id", "created_at", "finished_at")},
                  steps=to_public_steps(run.get("state_json")) if with_steps else None)
