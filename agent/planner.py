"""V5 — planning: decompose, order by dependency, execute, replan on failure.

A plan is a small DAG of tasks. Why a DAG and not a list? Because "convert the distance" and "look up the price" are independent
while "multiply the result" depends on the first — the structure tells the runtime what is safe to run in what order, what to skip
when a dependency fails, and what to ask the model to repair. The model proposes the plan as STRUCTURED output; the runtime
validates it (unknown deps, cycles) before executing anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .model import Message, ModelProvider
from .structured import generate_structured
from .tools import ToolRegistry

PLAN_SCHEMA = {"type": "object", "required": ["tasks"], "properties": {"tasks": {"type": "array", "items": {
    "type": "object", "required": ["id", "tool", "args"], "properties": {
        "id": {"type": "string"}, "tool": {"type": "string"}, "args": {"type": "object"},
        "depends_on": {"type": "array", "items": {"type": "string"}}}}}}}


@dataclass
class Task:
    id: str
    tool: str
    args: Dict[str, Any]
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"          # pending | done | failed | skipped
    result: str = ""
    error: str = ""


class PlanError(Exception):
    pass


def build_plan(raw: Dict[str, Any], registry: ToolRegistry) -> List[Task]:
    tasks = [Task(t["id"], t["tool"], t["args"], list(t.get("depends_on", []))) for t in raw["tasks"]]
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise PlanError("duplicate task ids")
    for t in tasks:
        if registry.get(t.tool) is None:
            raise PlanError(f"task {t.id} uses unknown tool '{t.tool}'")
        for d in t.depends_on:
            if d not in ids:
                raise PlanError(f"task {t.id} depends on unknown task '{d}'")
    topo_order(tasks)            # raises on cycles
    return tasks


def topo_order(tasks: List[Task]) -> List[Task]:
    by_id = {t.id: t for t in tasks}
    out, state = [], {}

    def visit(t: Task):
        if state.get(t.id) == 2:
            return
        if state.get(t.id) == 1:
            raise PlanError(f"dependency cycle through '{t.id}'")
        state[t.id] = 1
        for d in t.depends_on:
            visit(by_id[d])
        state[t.id] = 2
        out.append(t)
    for t in tasks:
        visit(t)
    return out


def substitute(args: Dict[str, Any], results: Dict[str, str]) -> Dict[str, Any]:
    """'$t1' inside a string argument is replaced by task t1's result, so later tasks can use earlier output."""
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("$") and v[1:] in results:
            raw = results[v[1:]]
            try:
                out[k] = float(raw) if "." in raw or raw.lstrip("-").isdigit() else raw
            except ValueError:
                out[k] = raw
        elif isinstance(v, str):
            for rid, rv in results.items():
                v = v.replace("$" + rid, rv)
            out[k] = v
        else:
            out[k] = v
    return out


class PlanExecutor:
    def __init__(self, registry: ToolRegistry, model: Optional[ModelProvider] = None, max_replans: int = 1):
        self.registry, self.model, self.max_replans = registry, model, max_replans
        self.log: List[str] = []

    def execute(self, tasks: List[Task]) -> List[Task]:
        replans = 0
        results: Dict[str, str] = {}
        while True:
            failed = None
            for t in topo_order(tasks):
                if t.status in ("done", "skipped"):
                    continue
                if any(next(x for x in tasks if x.id == d).status != "done" for d in t.depends_on):
                    t.status, t.error = "skipped", "a dependency did not complete"
                    continue
                r = self.registry.execute(t.tool, substitute(t.args, results))
                if r.ok:
                    t.status, t.result = "done", r.output
                    results[t.id] = r.output
                    self.log.append(f"{t.id}: ok")
                else:
                    t.status, t.error = "failed", r.error
                    self.log.append(f"{t.id}: failed ({r.error[:60]})")
                    failed = failed or t         # keep going: independent tasks still run, dependents get 'skipped'
            if failed is None or self.model is None or replans >= self.max_replans:
                return tasks
            replans += 1
            self.log.append(f"replanning after {failed.id}")
            ctx = Message("user", f"Task {failed.id} ({failed.tool} {failed.args}) failed: {failed.error}. Completed so far: {results}. "
                                  "Return a corrected plan for the REMAINING work only.")
            raw, _, _ = generate_structured(self.model, [ctx], PLAN_SCHEMA)
            new = build_plan(raw, self.registry)
            tasks = [t for t in tasks if t.status == "done"] + new
