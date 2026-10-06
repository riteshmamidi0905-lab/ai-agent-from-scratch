"""V6 — evaluating an agent.

Evaluating an agent is different from evaluating a model: the unit under test is a RUN. We score each run on things that are
checkable without a judge model, and report them per case and in aggregate:
  status_ok            did the run end in the status the case EXPECTS? (a runaway agent that is stopped counts as OK for a case that expects 'stopped')
  tool_correct         did it use exactly the expected tools, in order?
  adherent             does the answer contain required text and avoid forbidden text?
  grounded             every number in the answer appears in a tool output or the objective (catches invented figures)
  latency_ms, tokens, steps, failures
Deterministic checks are cheap, repeatable and CI-safe; they cannot judge fluency or reasoning quality — a model-based judge
would add that (see docs/learning-notes/06-evaluation.md).
"""
from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .state import RunState

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class EvalCase:
    name: str
    objective: str
    expected_tools: Optional[List[str]] = field(default_factory=list)      # None = don't check
    must_include: List[str] = field(default_factory=list)
    must_not_include: List[str] = field(default_factory=list)
    expect_status: str = "completed"
    check: Optional[Callable[[object, RunState], bool]] = None     # extra invariant, e.g. 'no file was written'; gets (agent, final state)
    agent_factory: Optional[Callable[[], object]] = None     # per-case agent (e.g. a scripted misbehaving model); default = the suite's factory


def _numbers(s: str) -> set:
    return {round(float(x), 3) for x in _NUM.findall(s.replace(",", ""))}


def score(case: EvalCase, state: RunState, latency_ms: float, agent: object = None) -> Dict:
    tools = [s.detail["tool"] for s in state.steps if s.kind == "tool"]
    ans = state.final_answer or ""
    evidence = _numbers(case.objective).union(*[_numbers(s.detail.get("output", "")) for s in state.steps if s.kind == "tool" and s.detail.get("ok")]) if state.steps else _numbers(case.objective)
    ungrounded = sorted(_numbers(ans) - evidence)
    return {
        "case": case.name, "status": state.status, "stop_reason": state.stop_reason,
        "status_ok": state.status == case.expect_status, "expected_status": case.expect_status,
        "reached_final_answer": state.status == "completed",
        "invariant_ok": True if case.check is None else bool(case.check(agent, state)),
        "tool_correct": True if case.expected_tools is None else tools == case.expected_tools,
        "adherent": all(x.lower() in ans.lower() for x in case.must_include) and not any(x.lower() in ans.lower() for x in case.must_not_include),
        "grounded": not ungrounded if case.expect_status == "completed" else None,
        "ungrounded_numbers": ungrounded,
        "latency_ms": round(latency_ms, 3), "tokens": state.usage.total, "steps": sum(1 for s in state.steps if s.kind == "model"),
        "failures": [e["kind"] for e in state.errors],
    }


def run_suite(make_agent: Callable[[], "object"], cases: List[EvalCase]) -> Dict:
    rows = []
    for c in cases:
        agent = (c.agent_factory or make_agent)()
        t0 = time.perf_counter()
        st = agent.run(c.objective)
        rows.append(score(c, st, (time.perf_counter() - t0) * 1000, agent))
    n = len(rows) or 1
    lat = sorted(r["latency_ms"] for r in rows)
    agg = {
        "cases": len(rows),
        "status_as_expected_rate": sum(r["status_ok"] for r in rows) / n,
        "final_answer_rate": sum(r["reached_final_answer"] for r in rows) / n,
        "tool_correctness": sum(r["tool_correct"] for r in rows) / n,
        "invariants_held": sum(r["invariant_ok"] for r in rows) / n,
        "instruction_adherence": sum(r["adherent"] for r in rows) / n,
        "groundedness": (lambda g: sum(g) / len(g) if g else None)([r["grounded"] for r in rows if r["grounded"] is not None]),
        "latency_ms_mean": round(statistics.mean(lat), 3) if lat else 0, "latency_ms_p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0,
        "tokens_total": sum(r["tokens"] for r in rows),
    }
    return {"aggregate": agg, "rows": rows}


def to_markdown(report: Dict) -> str:
    a = report["aggregate"]
    out = ["| metric | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in a.items()]
    out += ["", "| case | expected | actual (stop reason) | status ok | tools ok | invariant | adherent | grounded | steps | tokens |", "|---|---|---|---|---|---|---|---|---|---|"]
    out += [f"| {r['case']} | {r['expected_status']} | {r['status']} ({r['stop_reason']}) | {r['status_ok']} | {r['tool_correct']} | {r['invariant_ok']} | {r['adherent']} | {r['grounded']} | {r['steps']} | {r['tokens']} |" for r in report["rows"]]
    return "\n".join(out)


def evaluate_run(state: RunState) -> Dict:
    """Case-free, deterministic checks that apply to ANY finished run (used for the per-run 'evaluation' event):
    did it complete, are the numbers in the answer supported by the objective/tool outputs, how many tool calls failed."""
    tool_steps = [s for s in state.steps if s.kind == "tool"]
    evidence = set(_numbers(state.objective))
    for s in tool_steps:
        if s.detail.get("ok"):
            evidence |= _numbers(s.detail.get("output", ""))
    ans = state.final_answer or ""
    ungrounded = sorted(_numbers(ans) - evidence) if state.status == "completed" else []
    return {
        "completed": state.status == "completed", "status": state.status, "stop_reason": state.stop_reason,
        "grounded": (not ungrounded) if state.status == "completed" else None, "ungrounded_numbers": ungrounded,
        "tool_calls": len(tool_steps), "tool_failures": sum(1 for s in tool_steps if not s.detail.get("ok")),
        "model_calls": sum(1 for s in state.steps if s.kind == "model"), "tokens": state.usage.total,
    }
