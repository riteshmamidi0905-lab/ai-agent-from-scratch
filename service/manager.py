"""RunManager: the only place that knows both the runtime and the store. It builds an Agent per run, executes it on a worker thread,
persists every structured event (the public stream vocabulary) and the final state, and implements human approval through the store
(so an approval can be decided by any process, and an undecided one is denied after a timeout — fail closed)."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from agent.evals import evaluate_run
from agent.loop import Agent
from agent.model import ModelProvider, OpenAICompatProvider, RuleModel
from agent.reliability import Budget
from agent.security import Policy, redact
from agent.tools import add_file_tools, default_registry
from agent.trace import Tracer

from . import logs
from .config import Settings
from .store import Store

L = logging.getLogger("service.manager")

# core tracer event  ->  public stream event.  Anything not listed is not streamed.
PUBLIC = {"model_request": "model_request", "model_call": "model_response", "tool_requested": "tool_requested", "approval_required": "approval_required",
          "approval_decision": "approval_decision", "policy": "policy", "tool_call": "tool_completed", "retry": "retry", "failure": "failure"}
_DROP_FIELDS = {"run", "type"}          # `run` is redundant in a per-run stream


class TooManyRuns(Exception):
    pass


def make_provider(s: Settings) -> ModelProvider:
    return RuleModel() if s.provider == "rule" else OpenAICompatProvider(s.base_url, s.model, s.api_key)


class RunManager:
    def __init__(self, settings: Settings, store: Store, provider_factory: Optional[Callable[[], ModelProvider]] = None, poll_s: float = 0.05):
        self.s, self.store, self.poll_s = settings, store, poll_s
        self.provider_factory = provider_factory or (lambda: make_provider(settings))
        self.pool = ThreadPoolExecutor(max_workers=settings.max_concurrent_runs, thread_name_prefix="run")
        self._active = 0
        self._lock = threading.Lock()

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

    # -- submission --------------------------------------------------------------------------------------------
    def submit(self, objective: str, max_steps: Optional[int], request_id: str = "") -> Dict[str, Any]:
        steps = min(max_steps or self.s.max_steps_cap, self.s.max_steps_cap)
        with self._lock:
            if self._active >= self.s.max_concurrent_runs:
                raise TooManyRuns()
            self._active += 1
        try:
            run = self.store.create_run(redact(objective), steps, request_id)      # never persist a secret the user pasted into an objective
            self.store.append_event(run["id"], "run_queued", {"max_steps": steps})
            self.pool.submit(self._execute, run["id"], objective, steps)
        except BaseException:
            with self._lock:
                self._active -= 1
            raise
        return run

    # -- one run -----------------------------------------------------------------------------------------------
    def _build_agent(self, max_steps: int, tracer: Tracer, approver) -> Agent:
        reg = default_registry()
        if self.s.enable_file_tools:
            import os
            os.makedirs(self.s.workspace, exist_ok=True)
            add_file_tools(reg, self.s.workspace)
        return Agent(self.provider_factory(), reg, policy=Policy(approver=approver, block_injection=False), budget=Budget(max_steps=max_steps), tracer=tracer)

    def _execute(self, run_id: str, objective: str, max_steps: int) -> None:
        logs.run_id_var.set(run_id)
        store, ctx = self.store, {"approval": None}
        started = time.monotonic()

        def emit(type_: str, payload: Dict[str, Any]) -> None:
            try:
                store.append_event(run_id, type_, payload)
            except Exception:                                               # noqa: BLE001 - never let streaming kill the run
                L.exception("event write failed")

        def listener(ev: Dict[str, Any]) -> None:
            pub = PUBLIC.get(ev["type"])
            if not pub:
                return
            payload = {k: v for k, v in ev.items() if k not in _DROP_FIELDS}
            if ev["type"] == "approval_required":
                aid = store.create_approval(run_id, ev["tool"], ev.get("args", {}))
                ctx["approval"] = aid
                payload["approval_id"] = aid
                store.update_run(run_id, status="awaiting_approval")
            elif ev["type"] == "approval_decision":
                payload["approval_id"] = ctx["approval"]
                store.update_run(run_id, status="running")
            emit(pub, payload)

        def approver(name: str, args: Dict[str, Any]) -> bool:
            aid, deadline = ctx["approval"], time.monotonic() + self.s.approval_timeout_s
            while time.monotonic() < deadline:
                a = store.get_approval(aid)
                if a and a["status"] != "pending":
                    return a["status"] == "approved"
                time.sleep(self.poll_s)
            store.decide_approval(aid, False)                               # expired => denied (fail closed)
            emit("approval_expired", {"approval_id": aid, "tool": name})
            return False

        try:
            store.update_run(run_id, status="running")
            emit("run_started", {"objective": redact(objective)[:200]})
            agent = self._build_agent(max_steps, Tracer(run_id, listeners=[listener]), approver)
            state = agent.run(objective)
            ev = evaluate_run(state)
            emit("evaluation", ev)
            store.update_run(run_id, status=state.status, stop_reason=state.stop_reason, final_answer=redact(state.final_answer) if state.final_answer else None,
                             state_json=state.to_json(), evaluation=ev, tokens=state.usage.total, finished_at=_now())
            emit("run_completed", {"status": state.status, "stop_reason": state.stop_reason, "final_answer": redact(state.final_answer) if state.final_answer else None,
                                   "tokens": state.usage.total, "duration_ms": round((time.monotonic() - started) * 1000, 1)})
            logs.log(L, "run finished", status=state.status, stop_reason=state.stop_reason, tokens=state.usage.total)
        except Exception as e:                                              # noqa: BLE001 - an infrastructure failure must still end the run visibly
            L.exception("run crashed")
            try:
                store.update_run(run_id, status="failed", stop_reason="internal_error", finished_at=_now())
                emit("run_completed", {"status": "failed", "stop_reason": "internal_error", "error": type(e).__name__})
            except Exception:                                               # noqa: BLE001
                pass
        finally:
            with self._lock:
                self._active -= 1

    @property
    def active(self) -> int:
        return self._active


def _now() -> str:
    from .store import now
    return now()
