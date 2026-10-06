# Configuration, secrets and deployment

All settings are environment variables (`service/config.py`). Secrets are only ever read from the environment; nothing secret is in the image, the compose file or the repo (`.env` is git-ignored; `.env.example` has placeholders).

| Variable | Default | Secret | Meaning |
|---|---|---|---|
| `DATABASE_URL` | empty → in-memory store | yes (contains password) | PostgreSQL DSN. Empty = non-durable dev mode |
| `AGENT_API_TOKEN` | empty → no auth | **yes** | bearer token for `/v1/*`; **set it in any shared environment** |
| `AGENT_PROVIDER` | `rule` | | `rule` (offline, deterministic) or `openai_compat` |
| `AGENT_BASE_URL`, `AGENT_MODEL` | OpenAI URL, `gpt-4o-mini` | | model server; **server-side config only** (clients cannot choose a URL, so no SSRF) |
| `AGENT_API_KEY` | empty | **yes** | provider credential |
| `AGENT_WORKSPACE`, `AGENT_ENABLE_FILE_TOOLS` | `./workspace`, true | | root the file tools are confined to |
| `AGENT_MAX_CONCURRENT_RUNS`, `AGENT_MAX_STEPS_CAP`, `AGENT_MAX_OBJECTIVE_CHARS` | 4, 20, 2000 | | hard limits |
| `AGENT_APPROVAL_TIMEOUT_S` | 300 | | undecided approvals are denied after this |
| `LOG_LEVEL` | INFO | | |

## Run it
```bash
cp .env.example .env            # set POSTGRES_PASSWORD and AGENT_API_TOKEN
docker compose --env-file .env up --build
python3 scripts/smoke.py http://localhost:8000      # readiness, auth, run, SSE, persistence
```
Without Docker: `pip install ".[service]"`, set `DATABASE_URL`, `python -m service`.

## Persistence (PostgreSQL)
Tables `runs`, `run_events`, `approvals`, `eval_runs` (`service/migrations/001_init.sql`). The migrator (`service/migrate.py`) applies numbered SQL files once each under a Postgres advisory lock, so several replicas can start together. Event sequence numbers are allocated atomically (`UPDATE runs SET event_seq = event_seq + 1 … RETURNING`). On start, runs left `queued/running/awaiting_approval` by a dead process are marked `failed (interrupted)` rather than hanging forever.

## Redis: deliberately not used
No requirement justifies it: events and approvals are coordinated through PostgreSQL (SSE polls by sequence number), there is no job queue (runs execute on a bounded worker pool in the receiving process), and nothing needs sub-millisecond caching. Add Redis only if (a) runs must survive process restarts and be picked up by another worker (then a queue is needed), or (b) many API replicas must stream the same hot run (then pub/sub beats polling).

## Operations
- **Logs:** one JSON object per line to stdout with `request_id` and `run_id`; secrets are redacted before any trace/state is stored.
- **Health:** container `HEALTHCHECK` hits `/healthz`; orchestrators should gate traffic on `/readyz`.
- **Known limits:** runs execute in the process that accepted them (a restart interrupts them); one shared token; no cancel endpoint.
