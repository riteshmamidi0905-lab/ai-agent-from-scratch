"""V0 — structured output.

A model returns TEXT. Software needs DATA. The bridge is: ask for JSON, parse it, validate it against a schema, and if that fails,
tell the model exactly what was wrong and ask again (bounded). The validator below implements the small JSON-Schema subset
this runtime needs (type, required, properties, enum, minimum/maximum, items) so tool arguments and model outputs share one checker.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .model import Message, ModelProvider, ProviderError, Usage

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list, "null": type(None)}


def validate(value: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Return a list of human-readable problems ([] = valid)."""
    errs: List[str] = []
    t = schema.get("type")
    if t:
        py = _TYPES[t]
        ok = isinstance(value, py) and not (t in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing required '{k}'")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected field '{k}'")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Models wrap JSON in prose or code fences. Pull the first JSON object out; raise ValueError if there is none."""
    m = _FENCE.search(text)
    candidate = m.group(1) if m else text
    start = candidate.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(candidate[start:i + 1])
    raise ValueError("unterminated JSON object")


def generate_structured(provider: ModelProvider, messages: List[Message], schema: Dict[str, Any], max_repairs: int = 2) -> Tuple[Dict[str, Any], Usage, int]:
    """Ask for JSON matching `schema`. Returns (data, total_usage, repairs_used). Raises ProviderError(kind='structured') if it never validates."""
    total = Usage()
    convo = list(messages) + [Message("system", "Reply with ONE JSON object matching this schema, nothing else:\n" + json.dumps(schema))]
    last_problem = ""
    for attempt in range(max_repairs + 1):
        resp = provider.complete(convo)
        total.add(resp.usage)
        try:
            data = extract_json(resp.content)
            problems = validate(data, schema)
        except (ValueError, json.JSONDecodeError) as e:
            data, problems = None, [f"not valid JSON ({e})"]
        if not problems:
            return data, total, attempt
        last_problem = "; ".join(problems)
        convo += [Message("assistant", resp.content), Message("user", "That reply was invalid: " + last_problem + ". Reply again with corrected JSON only.")]
    raise ProviderError("model never produced valid structured output: " + last_problem, retryable=False, kind="structured")
