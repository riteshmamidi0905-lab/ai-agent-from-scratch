# HTTP API (V10) and event stream (V11)

Base path `/v1`. All `/v1/*` routes require `Authorization: Bearer <AGENT_API_TOKEN>` when that variable is set. `/healthz` and `/readyz` are always open. Every response carries `X-Request-ID` (a caller-supplied id is accepted after sanitising, otherwise generated); error bodies always have one shape:

```json
{"error": {"code": "validation_error | unauthorized | not_found | too_many_runs | already_decided | internal_error", "message": "…", "request_id": "…"}}
```

| Method & path | Purpose | Notes |
|---|---|---|
| `GET /healthz` | liveness | never touches dependencies |
| `GET /readyz` | readiness | 503 if the database is unreachable; returns non-secret config |
| `POST /v1/runs` | create a run → **202** | body `{objective, max_steps?}`; unknown fields rejected; steps capped by the server; **429** at the concurrent-run limit |
| `GET /v1/runs`, `GET /v1/runs/{id}` | list / status | the single-run view includes public steps and the run's evaluation |
| `GET /v1/runs/{id}/events?after=N` | events as JSON | resumable by sequence number |
| `GET /v1/runs/{id}/stream` | **Server-Sent Events** | `id:` = sequence; reconnect with `Last-Event-ID` (or `?after=`); heartbeat comments; ends after `run_completed` |
| `GET /v1/runs/{id}/approvals`, `POST /v1/runs/{id}/approvals/{aid}` | human approval | body `{approved: bool}`; a decision is final (**409** on a second one) |
| `POST /v1/evals/run`, `GET /v1/evals` | run / list the deterministic suite | results are stored |

## Public event vocabulary
`run_queued · run_started · model_request · model_response · tool_requested · approval_required (with approval_id) · approval_decision · approval_expired · policy · tool_completed · retry · failure · evaluation · run_completed`

`model_request`/`model_response` carry counts, durations and token usage — **never message text**. `tool_requested` carries the tool name and (redacted) arguments; `tool_completed` carries ok/duration. Free text a model emits alongside tool calls is dropped from steps and events, because it may contain reasoning that is not published. Only the final answer text appears (in `run_completed` and the run record).

## Why SSE and not WebSockets
The stream is one-directional (server → client); approvals are ordinary POSTs. SSE gives automatic reconnect with `Last-Event-ID`, works through proxies and `curl`, and needs no framing code. WebSockets would add bidirectional complexity with no requirement that needs it.

## Human approval flow
Write-level tools (e.g. `write_file`) pause the run (`status: awaiting_approval`) and emit `approval_required` with an `approval_id`. A client decides via POST. No decision within `AGENT_APPROVAL_TIMEOUT_S` ⇒ **denied** and `approval_expired` is emitted (fail closed).

## Not implemented (deliberately)
- Run cancellation: the runtime has no cooperative-cancel hook yet; a run ends by completion, budget, loop detection or failure.
- Multi-tenant isolation / per-user ownership of runs: one shared bearer token.
- Rate limiting beyond the concurrent-run cap.
