"""V4 — memory. "Memory" is four different things; confusing them is the most common design mistake.

  ConversationMemory  the recent messages sent back to the model each turn. Bounded: oldest turns are dropped first.
  WorkingMemory       a scratchpad for ONE run (intermediate values the runtime wants to keep). Dies with the run.
  PersistentMemory    facts that survive across runs, in a JSON file. Explicit key/value: the agent decides what to remember.
  SemanticMemory      "find what is relevant to this", by meaning-ish similarity. Here a hashed bag-of-words embedding + cosine
                      (an offline stand-in for a real embedding model — same interface, weaker quality).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .model import Message, estimate_tokens


class ConversationMemory:
    def __init__(self, max_tokens: int = 2000):
        self.max_tokens = max_tokens
        self.messages: List[Message] = []

    def add(self, m: Message) -> None:
        self.messages.append(m)

    def window(self) -> List[Message]:
        """Newest-first until the token budget is spent; never starts the window on an orphan tool message; keeps the system prompt."""
        sys_msgs = [m for m in self.messages if m.role == "system"]
        rest = [m for m in self.messages if m.role != "system"]
        budget = self.max_tokens - sum(estimate_tokens(m.content) for m in sys_msgs)
        kept: List[Message] = []
        for m in reversed(rest):
            cost = estimate_tokens(m.content) + 4
            if cost > budget and kept:
                break
            budget -= cost
            kept.append(m)
        kept.reverse()
        while kept and kept[0].role == "tool":      # a tool result without the assistant call that produced it is invalid
            kept.pop(0)
        return sys_msgs + kept


class WorkingMemory:
    def __init__(self):
        self._d: Dict[str, Any] = {}

    def set(self, k: str, v: Any) -> None:
        self._d[k] = v

    def get(self, k: str, default: Any = None) -> Any:
        return self._d.get(k, default)

    def dump(self) -> Dict[str, Any]:
        return dict(self._d)


class PersistentMemory:
    def __init__(self, path: str):
        self.path = path
        self._d: Dict[str, str] = {}
        if os.path.exists(path):
            with open(path) as f:
                self._d = json.load(f)

    def remember(self, key: str, value: str) -> None:
        self._d[key] = value
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._d, f, indent=2)
        os.replace(tmp, self.path)           # atomic: a crash cannot leave half a file

    def recall(self, key: str) -> Optional[str]:
        return self._d.get(key)

    def all(self) -> Dict[str, str]:
        return dict(self._d)


_WORD = re.compile(r"[a-z0-9]+")
_DIM = 256


def embed(text: str) -> List[float]:
    v = [0.0] * _DIM
    for w in _WORD.findall(text.lower()):
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % _DIM] += 1.0 if (h >> 100) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class SemanticMemory:
    def __init__(self):
        self.items: List[Tuple[str, List[float]]] = []

    def add(self, text: str) -> None:
        self.items.append((text, embed(text)))

    def search(self, query: str, k: int = 3, min_score: float = 0.15) -> List[Tuple[str, float]]:
        q = embed(query)
        scored = sorted(((t, sum(a * b for a, b in zip(q, e))) for t, e in self.items), key=lambda x: -x[1])
        return [(t, round(s, 4)) for t, s in scored[:k] if s >= min_score]
