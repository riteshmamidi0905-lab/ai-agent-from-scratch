# ai-agent-from-scratch

An AI agent runtime built up one idea at a time, a **standard-library-only core** (no framework, no dependencies; later service layers add optional dependencies), so every line can be read and explained. Each module is one concept; `docs/learning-notes/` explains *why* it exists and how to answer the matching interview question.

```bash
python3 -m unittest discover -s tests          # core runtime, no dependencies
python3 examples/run_eval.py                   # deterministic eval suite -> docs/eval-report.md
pip install ".[service,dev]" && python -m unittest discover -s tests     # + HTTP service tests
AGENT_TEST_DATABASE_URL=postgresql://... python -m unittest discover -s tests   # + PostgreSQL integration tests
docker compose --env-file .env up --build      # API + PostgreSQL (see docs/configuration.md)
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
| V10 | FastAPI service: typed schemas, auth, errors, health/readiness, approvals | `service/app.py`, `service/manager.py` | done, tested |
| V11 | structured execution events over SSE (resumable, no model text) | `service/app.py` | done, tested |
| V12 | PostgreSQL + migrations, Docker/Compose, structured logs, CI incl. Docker smoke | `service/pg_store.py`, `Dockerfile`, `docker-compose.yml` | done; tested against real PostgreSQL |

## What is honest about it
- **No real LLM is used in tests, in CI or in the eval report.** The real-provider demo exists but has not been run here (no credentials were available). `RuleModel` is a rule-based stand-in; `ScriptedModel` replays fixed turns. The eval report therefore measures the *runtime*, not a model.
- `OpenAICompatProvider` speaks the OpenAI chat-completions shape and is tested against a local HTTP server, **not** against OpenAI/Ollama. Treat it as untested against real vendors until a recorded run exists.
- See `docs/architecture.md` for invariants and known limits (thread timeouts, estimated tokens, per-run idempotency).
- Injection scanning is a heuristic; the real protection is the policy/approval/sandbox layer (see note 06).
- The embedding in `SemanticMemory` is a hashed bag of words, not a learned model.

## Service
See `docs/api.md` (endpoints, event vocabulary, SSE rationale), `docs/configuration.md` (env vars, secrets, Postgres, why no Redis). Real-model demo: `examples/real_provider_demo.py` (env-supplied credentials; optional test `tests/test_real_provider.py`, skipped in CI).

## Layout
`agent/` the runtime · `service/` HTTP + persistence layer · `tests/` (stdlib `unittest`) · `docs/learning-notes/` concept notes · `examples/run_eval.py`.

## Next
A comparison against OpenAI Agents SDK / LangGraph / MCP (documented trade-offs, not benchmarks).
