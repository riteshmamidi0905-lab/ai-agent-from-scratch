"""V7 — reliability.

Models and networks fail in boring, repeated ways. The runtime handles each one explicitly instead of hoping:
  retry_call        bounded retries with exponential backoff, ONLY for errors marked retryable (a 400 will not get better).
  IdempotencyCache  a tool call with a given id runs at most once; a retried/duplicated call returns the stored result.
                    Essential for tools with side effects (sending, writing, paying).
  Budget            hard caps on steps and tokens so a confused agent stops instead of burning money.
  LoopDetector      the same tool with the same arguments N times in a row is a loop, not progress.
  EscalationRequired raised when the right move is "ask a human" rather than "try again".
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .model import ProviderError


class EscalationRequired(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def retry_call(fn: Callable[[], Any], attempts: int = 3, base_delay: float = 0.2, sleep: Callable[[float], None] = time.sleep,
               on_retry: Optional[Callable[[int, Exception], None]] = None) -> Any:
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return fn()
        except ProviderError as e:
            last = e
            if not e.retryable or i == attempts - 1:
                raise
            if on_retry:
                on_retry(i + 1, e)
            sleep(base_delay * (2 ** i))
    raise last  # pragma: no cover


class IdempotencyCache:
    def __init__(self):
        self._r: Dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        return self._r.get(key)

    def put(self, key: str, value: Any) -> None:
        self._r[key] = value

    @staticmethod
    def key(name: str, args: Dict[str, Any]) -> str:
        return name + ":" + json.dumps(args, sort_keys=True)


@dataclass
class Budget:
    max_steps: int = 10
    max_tokens: int = 20000

    def exceeded(self, steps: int, tokens: int) -> Optional[str]:
        if steps >= self.max_steps:
            return "step_limit"
        if tokens >= self.max_tokens:
            return "token_budget"
        return None


class LoopDetector:
    def __init__(self, threshold: int = 3):
        self.threshold, self.recent = threshold, []

    def observe(self, name: str, args: Dict[str, Any]) -> bool:
        """True when the identical call has now been made `threshold` times in a row."""
        sig = IdempotencyCache.key(name, args)
        self.recent.append(sig)
        self.recent = self.recent[-self.threshold:]
        return len(self.recent) == self.threshold and len(set(self.recent)) == 1
