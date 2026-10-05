"""V0 — the model interface.

An agent runtime should not know which model it talks to. Everything the loop needs from a model is:
  messages in  →  (text and/or tool calls) + token usage out.
That narrow contract is the provider abstraction. Swapping providers = writing one class.
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    id: str
    name: str
    args: Dict[str, Any]


@dataclass
class Message:
    role: str                       # system | user | assistant | tool
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)   # on assistant messages
    tool_call_id: Optional[str] = None                         # on tool messages
    name: Optional[str] = None                                 # tool name on tool messages


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class ProviderError(Exception):
    """A model call failed. `retryable` tells the reliability layer whether trying again can help."""

    def __init__(self, message: str, retryable: bool = False, kind: str = "provider"):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 characters per token). An ESTIMATE used when a provider reports no usage."""
    return max(1, (len(text) + 3) // 4) if text else 0


def messages_tokens(messages: List[Message]) -> int:
    return sum(estimate_tokens(m.content) + sum(estimate_tokens(json.dumps(c.args)) for c in m.tool_calls) for m in messages)


class ModelProvider(ABC):
    @abstractmethod
    def complete(self, messages: List[Message], tools: Optional[List[Dict[str, Any]]] = None) -> ModelResponse:
        """Return the model's next turn. `tools` are JSON-schema tool descriptions the model may call."""


class ScriptedModel(ModelProvider):
    """Replays a fixed list of responses. The workhorse of deterministic tests: the agent is the thing under test, not the model."""

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls: List[List[Message]] = []

    def complete(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self.script:
            raise ProviderError("script exhausted", retryable=False, kind="script")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            item = ModelResponse(content=item)
        if not item.usage.total:
            item.usage = Usage(messages_tokens(messages), estimate_tokens(item.content))
        return item


_ARITH = re.compile(r"(-?\d+(?:\.\d+)?)\s*([+\-*/x×])\s*(-?\d+(?:\.\d+)?)")
_KM = re.compile(r"(-?\d+(?:\.\d+)?)\s*km\b", re.I)


class RuleModel(ModelProvider):
    """A deterministic OFFLINE stand-in for a model. It is NOT an LLM: it pattern-matches the user's text to pick a tool,
    then turns the tool result into a sentence. It exists so the whole runtime can be exercised with no network or keys."""

    def __init__(self):
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return f"call_{self._n}"

    def complete(self, messages, tools=None):
        last = messages[-1]
        names = {t["name"] for t in (tools or [])}
        if last.role == "tool":
            body = re.sub(r"</?untrusted[^>]*>", "", last.content.split("\n[WARNING")[0]).strip()
            text = f"The result is {body}." if not body.startswith("ERROR") else f"I could not finish: {body}"
            return ModelResponse(text, usage=Usage(messages_tokens(messages), estimate_tokens(text)))
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        m = _ARITH.search(user)
        if m and "calculator" in names:
            op = {"x": "*", "×": "*"}.get(m.group(2), m.group(2))
            return ModelResponse(tool_calls=[ToolCall(self._id(), "calculator", {"expression": f"{m.group(1)} {op} {m.group(3)}"})],
                                 usage=Usage(messages_tokens(messages), 12))
        k = _KM.search(user)
        if k and "convert_units" in names:
            return ModelResponse(tool_calls=[ToolCall(self._id(), "convert_units", {"value": float(k.group(1)), "from": "km", "to": "mi"})],
                                 usage=Usage(messages_tokens(messages), 12))
        text = "I do not have a tool for that."
        return ModelResponse(text, usage=Usage(messages_tokens(messages), estimate_tokens(text)))


class OpenAICompatProvider(ModelProvider):
    """Talks to any server that speaks the OpenAI chat-completions JSON shape (OpenAI, Ollama, vLLM, LM Studio...).
    Uses only urllib. Tested against a local HTTP server in tests/, not against a paid API."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 30.0):
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout

    @staticmethod
    def _wire(m: Message) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in m.tool_calls]
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        return d

    def complete(self, messages, tools=None):
        body: Dict[str, Any] = {"model": self.model, "messages": [self._wire(m) for m in messages]}
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + self.api_key} if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            e.close()
            raise ProviderError(f"HTTP {e.code}", retryable=e.code in (408, 429, 500, 502, 503, 504), kind="http")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ProviderError(f"network: {e}", retryable=True, kind="network")
        except json.JSONDecodeError:
            raise ProviderError("provider returned non-JSON", retryable=True, kind="malformed")
        try:
            msg = data["choices"][0]["message"]
            calls = [ToolCall(c["id"], c["function"]["name"], _loads(c["function"]["arguments"])) for c in (msg.get("tool_calls") or [])]
            u = data.get("usage") or {}
            usage = Usage(u.get("prompt_tokens", messages_tokens(messages)), u.get("completion_tokens", estimate_tokens(msg.get("content") or "")))
            return ModelResponse(msg.get("content") or "", calls, usage)
        except (KeyError, IndexError, TypeError):
            raise ProviderError("unexpected response shape", retryable=False, kind="malformed")


def _loads(s: Any) -> Dict[str, Any]:
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {"_raw": s}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": s}          # malformed arguments stay visible so the loop can report them to the model
