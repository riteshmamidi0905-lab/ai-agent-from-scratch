"""FastAPI application. HTTP concerns only: validation, auth, ids, errors, SSE framing. Execution lives in RunManager; the runtime in agent/."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent.evals import run_suite
from . import logs
from .config import Settings
from .manager import RunManager, TooManyRuns
from .schemas import ApprovalDecision, ApprovalOut, EventOut, RunCreate, RunOut, run_out
from .store import MemoryStore, Store

L = logging.getLogger("service.http")
TERMINAL = ("completed", "failed", "stopped", "escalated")


def create_app(settings: Optional[Settings] = None, store: Optional[Store] = None, manager: Optional[RunManager] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    if store is None:
        if settings.database_url:
            from .pg_store import PostgresStore
            store = PostgresStore(settings.database_url)
        else:
            store = MemoryStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logs.setup(settings.log_level)
        store.init()
        n = store.mark_interrupted()                  # runs a previous process left active can never finish: say so, don't hang forever
        if n:
            L.warning("marked interrupted runs", extra={"fields": {"count": n}})
        app.state.manager = manager or RunManager(settings, store)
        logs.log(L, "service started", **settings.public())
        yield
        app.state.manager.shutdown()
        store.close()

    app = FastAPI(title="ai-agent-from-scratch service", version="0.12.0", lifespan=lifespan)
    app.state.settings, app.state.store = settings, store

    # ---- request ids, structured request log ------------------------------------------------------------------
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        rid = "".join(ch for ch in rid if ch.isalnum() or ch in "-_")[:64] or uuid.uuid4().hex[:16]
        logs.request_id_var.set(rid)
        request.state.request_id = rid
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception:                                  # noqa: BLE001
            L.exception("unhandled error")
            resp = JSONResponse({"error": {"code": "internal_error", "message": "internal error", "request_id": rid}}, status_code=500)
        resp.headers["x-request-id"] = rid
        if request.url.path not in ("/healthz",):
            logs.log(L, "request", method=request.method, path=request.url.path, status=resp.status_code, ms=round((time.perf_counter() - t0) * 1000, 1))
        return resp

    def err(request: Request, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", "")}}, status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, e: StarletteHTTPException):
        code = {401: "unauthorized", 404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return err(request, e.status_code, code, str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, e: RequestValidationError):
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return err(request, 422, "validation_error", f"{loc}: {first.get('msg', 'invalid request')}")

    # ---- auth ------------------------------------------------------------------------------------------------
    def require_auth(authorization: Optional[str] = Header(default=None)):
        if not settings.api_token:
            return
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), settings.api_token.encode()):          # constant-time comparison
            raise HTTPException(401, "missing or invalid bearer token")

    def mgr(request: Request) -> RunManager:
        return request.app.state.manager

    def get_run_or_404(run_id: str):
        r = store.get_run(run_id)
        if r is None:
            raise HTTPException(404, f"run '{run_id}' not found")
        return r

    # ---- health ----------------------------------------------------------------------------------------------
    @app.get("/healthz", tags=["ops"])
    def healthz():
        """Liveness: the process is up. Never touches dependencies."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    def readyz(request: Request):
        """Readiness: dependencies are usable (database reachable). Returns 503 otherwise so an orchestrator stops routing traffic."""
        ok = store.ping()
        body = {"status": "ready" if ok else "not_ready", "checks": {"store": ok}, "config": settings.public(), "active_runs": mgr(request).active}
        return JSONResponse(body, status_code=200 if ok else 503)

    # ---- runs ------------------------------------------------------------------------------------------------
    @app.post("/v1/runs", status_code=202, response_model=RunOut, dependencies=[Depends(require_auth)], tags=["runs"])
    def create_run(body: RunCreate, request: Request):
        if len(body.objective) > settings.max_objective_chars:
            raise HTTPException(422, f"objective exceeds {settings.max_objective_chars} characters")
        try:
            run = mgr(request).submit(body.objective, body.max_steps, request.state.request_id)
        except TooManyRuns:
            return err(request, 429, "too_many_runs", "the server is at its concurrent-run limit; retry shortly")
        return run_out(run)

    @app.get("/v1/runs", response_model=List[RunOut], dependencies=[Depends(require_auth)], tags=["runs"])
    def list_runs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return [run_out(r) for r in store.list_runs(limit, offset)]

    @app.get("/v1/runs/{run_id}", response_model=RunOut, dependencies=[Depends(require_auth)], tags=["runs"])
    def get_run(run_id: str):
        return run_out(get_run_or_404(run_id), with_steps=True)

    @app.get("/v1/runs/{run_id}/events", response_model=List[EventOut], dependencies=[Depends(require_auth)], tags=["runs"])
    def run_events(run_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
        get_run_or_404(run_id)
        return store.events(run_id, after, limit)

    @app.get("/v1/runs/{run_id}/stream", dependencies=[Depends(require_auth)], tags=["runs"])
    async def stream(run_id: str, request: Request, last_event_id: Optional[str] = Header(default=None), after: int = Query(0, ge=0)):
        """Server-Sent Events. Each event: `id: <seq>`, `event: <type>`, `data: <json>`. Reconnect with Last-Event-ID (or ?after=) to resume
        without gaps. The stream ends after `run_completed`. Heartbeat comments keep proxies from closing idle connections."""
        get_run_or_404(run_id)
        try:
            cursor = int(last_event_id) if last_event_id else after
        except ValueError:
            cursor = after

        async def gen():
            nonlocal cursor
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                batch = await asyncio.to_thread(store.events, run_id, cursor, 200)
                for e in batch:
                    cursor = e["seq"]
                    idle = 0.0
                    yield f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps({**e['payload'], 'ts': e['ts']}, default=str)}\n\n"
                    if e["type"] == "run_completed":
                        return
                if not batch:
                    run = await asyncio.to_thread(store.get_run, run_id)
                    if run and run["status"] in TERMINAL and not await asyncio.to_thread(store.events, run_id, cursor, 1):
                        return                                   # finished and fully drained (e.g. interrupted run with no completion event)
                    idle += 0.1
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.1)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- approvals -------------------------------------------------------------------------------------------
    @app.get("/v1/runs/{run_id}/approvals", response_model=List[ApprovalOut], dependencies=[Depends(require_auth)], tags=["approvals"])
    def approvals(run_id: str):
        get_run_or_404(run_id)
        return store.list_approvals(run_id)

    @app.post("/v1/runs/{run_id}/approvals/{approval_id}", response_model=ApprovalOut, dependencies=[Depends(require_auth)], tags=["approvals"])
    def decide(run_id: str, approval_id: str, body: ApprovalDecision, request: Request):
        get_run_or_404(run_id)
        a = store.get_approval(approval_id)
        if a is None or a["run_id"] != run_id:
            raise HTTPException(404, "approval not found for this run")
        if not store.decide_approval(approval_id, body.approved):
            return err(request, 409, "already_decided", f"approval already {a['status']}")
        logs.log(L, "approval decided", approval_id=approval_id, approved=body.approved, tool=a["tool"])
        return store.get_approval(approval_id)

    # ---- evaluation ------------------------------------------------------------------------------------------
    @app.post("/v1/evals/run", dependencies=[Depends(require_auth)], tags=["evals"])
    def run_eval(request: Request):
        """Run the built-in deterministic suite against the offline RuleModel (measures the runtime, not an LLM) and store the result."""
        from agent.loop import Agent
        from agent.model import RuleModel
        from agent.tools import default_registry
        from .eval_cases import CASES
        rep = run_suite(lambda: Agent(RuleModel(), default_registry(), sleep=lambda s: None), CASES)
        eid = store.save_eval(rep["aggregate"], rep["rows"])
        return {"id": eid, **rep}

    @app.get("/v1/evals", dependencies=[Depends(require_auth)], tags=["evals"])
    def list_evals(limit: int = Query(10, ge=1, le=50)):
        return store.list_evals(limit)

    return app
