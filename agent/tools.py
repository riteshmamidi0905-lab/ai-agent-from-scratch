"""V1 — tool calling.

A tool is: a name, a description the model reads, a JSON schema for its arguments, and a Python function.
The registry is the ONLY way the model can cause anything to happen. Execution never raises into the loop: every outcome is a
ToolResult(ok, output, error, kind) so failures are DATA the model (and the tracer) can see and react to.
"""
from __future__ import annotations

import ast
import operator
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .structured import validate
from .security import LEVELS, PathSandbox
from .reliability import EscalationRequired


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    kind: str = ""          # '' on success, else a trace.FAILURE_KINDS value


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    fn: Callable[..., Any]
    level: str = "read"
    timeout: float = 5.0
    idempotent: bool = True

    def spec(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        assert tool.level in LEVELS, tool.level
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def specs(self, allow=None) -> List[Dict[str, Any]]:
        return [t.spec() for n, t in self._tools.items() if allow is None or n in allow]

    def execute(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """Validate then run. Never raises. Enforces the per-tool timeout by running in a worker thread."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"unknown tool '{name}'. Available: {sorted(self._tools)}", kind="validation")
        problems = validate(args, {**tool.parameters, "type": "object"})
        if "_raw" in args:
            problems = ["arguments were not valid JSON"]
        if problems:
            return ToolResult(False, error="invalid arguments: " + "; ".join(problems), kind="validation")
        box: Dict[str, Any] = {}

        def run():
            try:
                box["out"] = tool.fn(**args)
            except EscalationRequired as e:
                box["err"], box["kind"] = e.reason, "escalated"
            except PermissionError as e:
                box["err"], box["kind"] = f"permission: {e}", "denied"
            except Exception as e:           # noqa: BLE001 - tool bugs must not crash the agent
                box["err"], box["kind"] = f"{type(e).__name__}: {e}", "tool_error"

        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(tool.timeout)
        if th.is_alive():
            return ToolResult(False, error=f"tool '{name}' exceeded {tool.timeout}s", kind="tool_timeout")
        if "err" in box:
            return ToolResult(False, error=box["err"], kind=box["kind"])
        return ToolResult(True, output=str(box["out"]))


# ---- built-in example tools -------------------------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.USub: operator.neg, ast.Pow: operator.pow}


def safe_eval(expr: str) -> float:
    """Arithmetic only, via the AST — never eval(). A calculator tool is the classic place agents get remote code execution."""
    def ev(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 10:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("only + - * / ** and numbers are allowed")
    return ev(ast.parse(expr, mode="eval").body)


_UNITS = {("km", "mi"): 0.621371, ("mi", "km"): 1.609344, ("kg", "lb"): 2.204623, ("lb", "kg"): 0.453592}


def default_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(Tool("calculator", "Evaluate an arithmetic expression such as '3 * (4 + 5)'.",
                    {"properties": {"expression": {"type": "string"}}, "required": ["expression"], "additionalProperties": False},
                    lambda expression: round(safe_eval(expression), 6)))

    def convert(value, **kw):
        key = (kw["from"], kw["to"])
        if key not in _UNITS:
            raise ValueError(f"unsupported conversion {key}")
        return round(value * _UNITS[key], 6)
    r.register(Tool("convert_units", "Convert a value between km/mi or kg/lb.",
                    {"properties": {"value": {"type": "number"}, "from": {"type": "string", "enum": ["km", "mi", "kg", "lb"]},
                                    "to": {"type": "string", "enum": ["km", "mi", "kg", "lb"]}}, "required": ["value", "from", "to"],
                     "additionalProperties": False}, convert))
    return r


def add_file_tools(registry: ToolRegistry, root: str, max_bytes: int = 20000) -> PathSandbox:
    """Register read_file (read) and write_file (write) confined to `root`. Every path goes through PathSandbox.resolve, so
    '..', absolute paths and symlinks that leave the root raise PermissionError, which the registry reports as a 'denied' result."""
    sb = PathSandbox(root)

    def read_file(path: str) -> str:
        with open(sb.resolve(path), "rb") as f:
            data = f.read(max_bytes + 1)
        return data[:max_bytes].decode("utf-8", "replace") + ("\n[truncated]" if len(data) > max_bytes else "")

    def write_file(path: str, text: str) -> str:
        full = sb.resolve(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
        return f"wrote {len(text)} characters"

    registry.register(Tool("read_file", "Read a text file inside the workspace.", {"properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}, read_file))
    registry.register(Tool("write_file", "Write a text file inside the workspace.", {"properties": {"path": {"type": "string"}, "text": {"type": "string"}}, "required": ["path", "text"], "additionalProperties": False}, write_file, level="write", idempotent=False))
    return sb
