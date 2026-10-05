# 04 · Preventing infinite loops · recovering from failure

**Infinite loops** — three independent guards, because each catches a different failure:
- `Budget.max_steps`: hard cap on model calls.
- `Budget.max_tokens`: cap on spend (a loop with long outputs hits this first).
- `LoopDetector`: same tool + same arguments N times in a row = no progress; stop early with `loop_detected`.

**Tool failure** — the result goes back to the model as `ERROR: …` so it can change its approach (test: `test_tool_error_is_fed_back_and_agent_recovers`). Failure kinds are distinct: `validation` (bad args), `tool_error` (it raised), `tool_timeout` (worker thread exceeded its limit), `denied` (policy), `escalated` (needs a human).

**Provider failure** — `retry_call` retries only errors flagged `retryable` (timeouts, 429/5xx), with exponential backoff; a 400 or bad key fails immediately. Malformed structured output has its own bounded repair loop.

**Idempotency** — a repeated tool-call id returns the stored result instead of running again, so a retry or duplicate cannot send two emails.

**Escalation** — when the right move is "ask a human", a tool raises `EscalationRequired`; the run ends as `escalated` with the reason instead of guessing.
