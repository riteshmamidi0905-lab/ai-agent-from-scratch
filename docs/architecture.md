# Architecture

```
            ┌────────────────────────────── agent/ (core runtime, stdlib only) ──────────────────────────────┐
 objective ─►  Agent.run (loop.py)                                                                           │
            │    ├─ Budget / LoopDetector (reliability.py)       stop conditions, owned by software          │
            │    ├─ ModelProvider.complete (model.py)            ScriptedModel | RuleModel | OpenAICompat    │
            │    │     └─ retry_call (retryable errors only)                                                  │
            │    ├─ Policy.check (security.py)                   allow-list → level cap → approval (fail closed)
            │    ├─ ToolRegistry.execute (tools.py)              validate args → run in worker thread → timeout
            │    ├─ wrap_untrusted (security.py)                 tool output = labelled, redacted data       │
            │    ├─ ConversationMemory (memory.py)               system + pinned objective + newest window   │
            │    ├─ RunState (state.py)                          the one serialisable record of a run        │
            │    └─ Tracer (trace.py)                            structured events, redacted, JSONL sink     │
            └──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Data flow of one tool call
`model → ToolCall(id,name,args)` → duplicate-id check (`RunState.tool_results`) → `approval_required`? → `Policy.check` → `ToolRegistry.execute` (schema validation, thread, timeout) → `wrap_untrusted` (+ injection scan) → `tool` message appended → next model call.

## Invariants (each has a test)
1. A run always ends with a status and stop reason (`completed | failed | stopped | escalated`).
2. No tool runs unless it is registered, passes schema validation and passes policy.
3. Anything needing approval is denied when no approver exists.
4. File tools cannot leave their workspace root (real-path check).
5. Secret-looking strings are redacted before they reach a trace or a serialised `RunState`.
6. A repeated tool-call id never executes twice within a run.
7. The task objective is never evicted from the model's context window.

## Trust boundaries
Untrusted: model output (tool names/args), tool output, user text, files. Trusted: the runtime code, the `Policy`, the registry contents. The model never enforces a rule; the runtime does.

## Known limits
- A tool that exceeds its timeout is *reported* as timed out, but its worker thread cannot be killed; a side-effecting tool may still complete. Use idempotent tools or a business key.
- Token accounting uses provider-reported usage when present, otherwise a ~4 chars/token **estimate**. The token budget is checked before each model call, so a run can overshoot by one call.
- Idempotency is per run and per tool-call id, not per intended action.
- `LoopDetector` catches only *consecutive* identical calls; alternating A/B/A/B loops are caught by the step limit instead.
- Injection scanning is regex heuristics. The protection that matters is least privilege + approvals + sandboxing.
- `SemanticMemory` matches shared words (hashed bag of words), not meaning.
