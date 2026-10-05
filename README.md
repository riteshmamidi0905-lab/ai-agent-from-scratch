# ai-agent-from-scratch

An AI agent runtime built up one idea at a time, **standard library only** (no framework, no dependencies), so every line can be read and explained. Each module is one concept; `docs/learning-notes/` explains *why* it exists and how to answer the matching interview question.

```
python3 -m unittest discover -s tests     # 46 tests
python3 examples/run_eval.py              # writes docs/eval-report.md
```

## Status — what is built and what is not

| Version | Concept | Module | State |
|---|---|---|---|
| V0 | model interface, messages, structured output, token accounting, errors | `model.py`, `structured.py` | done, tested |
| V1 | tool registry, schemas, validation, execution, errors, timeouts | `tools.py` | done, tested |
| V2 | the agent loop | `loop.py` | done, tested |
| V3 | explicit, serialisable run state | `state.py` | done, tested |
| V4 | conversation / working / persistent / semantic memory | `memory.py` | done, tested |
| V5 | planning: DAG, ordering, execution, replanning | `planner.py` | done, tested |
| V6 | evaluation: completion, tool correctness, adherence, groundedness, latency, tokens | `evals.py` | done, tested |
| V7 | retries, timeouts, idempotency, budgets, loop detection, escalation | `reliability.py` | done, tested |
| V8 | permissions, approval gates, path sandbox, injection boundary, redaction | `security.py` | done, tested |
| V9 | structured events, spans, failure categories | `trace.py` | done, tested |
| V10 | FastAPI service | — | **not started** |
| V11 | streaming execution state (SSE/WebSocket) | — | **not started** |
| V12 | PostgreSQL, Docker, deployment, monitoring | — | **not started** |

## What is honest about it
- **No real LLM is used in tests or in the eval report.** `RuleModel` is a rule-based stand-in; `ScriptedModel` replays fixed turns. The eval report therefore measures the *runtime*, not a model.
- `OpenAICompatProvider` speaks the OpenAI chat-completions shape and is tested against a local HTTP server, **not** against OpenAI/Ollama. Treat it as untested against real vendors until a recorded run exists.
- Injection scanning is a heuristic; the real protection is the policy/approval/sandbox layer (see note 06).
- The embedding in `SemanticMemory` is a hashed bag of words, not a learned model.

## Layout
`agent/` the runtime · `tests/` unit tests (stdlib `unittest`) · `docs/learning-notes/` concept notes · `examples/run_eval.py`.

## Next
V10–V12, then a comparison against OpenAI Agents SDK / LangGraph / MCP (documented trade-offs, not benchmarks).
