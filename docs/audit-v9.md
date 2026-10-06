# Audit of V0–V9 before building V10–V12

Method: read every module and test as a reviewer would, checking each README/docstring claim against code. Findings and what was done:

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | `security.py` claimed secrets were redacted "on everything traced or returned"; only tool output was | claim not supported | Tracer and `RunState.to_json` now redact; test `test_secrets_never_reach_trace_or_serialised_state` |
| 2 | Path sandbox existed but no tool used it ("file tools" claim had no file tool) | claim not supported | `add_file_tools` (read_file read-level, write_file write-level) + agent-level escape tests incl. absolute path and `..` |
| 3 | `Agent.cache` / `IdempotencyCache` was never used; idempotency docs overstated | dead code + overclaim | removed; docs state exactly what is deduped (same call id within a run) |
| 4 | Objective could be evicted from the context window in long runs | real defect | first user message pinned; test |
| 5 | Replan failure (bad model output) raised out of `PlanExecutor` | real defect | caught, logged, executor returns; test |
| 6 | Tool failures were not in `RunState.errors` | gap | recorded; test |
| 7 | `SemanticMemory` used md5 and a threshold that passed by hash luck | fragile test, lint smell | blake2b, stop words, no random signs; test now passes for the right reason |
| 8 | No architecture doc; not installable as a package | gap | `docs/architecture.md`, `pyproject.toml` |
| 9 | Thread timeouts cannot kill a tool; token budget can overshoot by a call; per-run idempotency; consecutive-only loop detection | limitations | documented in architecture.md ("Known limits"), not hidden |

Not findings but worth stating: no real LLM has been exercised yet; `OpenAICompatProvider` is verified only against a local test server; the eval report measures the runtime with a rule-based model.
