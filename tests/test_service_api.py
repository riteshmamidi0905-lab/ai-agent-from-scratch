import json, os, tempfile, threading, time, unittest

try:
    from fastapi.testclient import TestClient
    from service.app import create_app
    from service.config import Settings
    from service.manager import RunManager
    from service.store import MemoryStore
    HAVE = True
except ImportError:                      # core CI job has no web dependencies
    HAVE = False

from agent.model import ModelProvider, ModelResponse, RuleModel, ScriptedModel, ToolCall


def wait_status(client, run_id, want, timeout=10, headers=None):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/v1/runs/{run_id}", headers=headers).json()
        if r["status"] in want:
            return r
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached {want}: {r['status']}")


def sse(client, url, headers=None):
    out, cur = [], {}
    with client.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith(":"):
                continue
            if line == "":
                if cur:
                    out.append(cur); cur = {}
                continue
            k, _, v = line.partition(": ")
            cur[k] = json.loads(v) if k == "data" else v
    return out


@unittest.skipUnless(HAVE, "service dependencies not installed")
class ApiBase(unittest.TestCase):
    token = ""
    provider = staticmethod(lambda: RuleModel())
    extra = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(workspace=self.tmp.name, api_token=self.token, approval_timeout_s=5.0, log_level="CRITICAL", **self.extra)
        self.store = MemoryStore()
        self.app = create_app(self.settings, self.store, manager=RunManager(self.settings, self.store, self.provider, poll_s=0.01))
        self.c = TestClient(self.app)
        self.c.__enter__()
        self.h = {"Authorization": "Bearer " + self.token} if self.token else {}

    def tearDown(self):
        self.c.__exit__(None, None, None)
        self.tmp.cleanup()

    def post_run(self, objective="compute 6 x 7", **kw):
        r = self.c.post("/v1/runs", json={"objective": objective, **kw}, headers=self.h)
        self.assertEqual(r.status_code, 202, r.text)
        return r.json()


class RunLifecycleTests(ApiBase):
    def test_health_and_readiness(self):
        self.assertEqual(self.c.get("/healthz").json(), {"status": "ok"})
        r = self.c.get("/readyz").json()
        self.assertEqual((r["status"], r["checks"]["store"]), ("ready", True))
        self.assertNotIn("api_key", json.dumps(r)); self.assertEqual(r["config"]["store"], "memory")
        self.store.ping = lambda: False
        self.assertEqual(self.c.get("/readyz").status_code, 503)

    def test_create_run_executes_and_is_evaluated(self):
        run = self.post_run()
        self.assertIn(run["status"], ("queued", "running"))
        done = wait_status(self.c, run["id"], ("completed",))
        self.assertIn("42", done["final_answer"])
        self.assertTrue(done["evaluation"]["grounded"]); self.assertEqual(done["evaluation"]["tool_failures"], 0)
        self.assertGreater(done["tokens"], 0)
        kinds = [s["kind"] for s in done["steps"]]
        self.assertEqual(kinds, ["model", "tool", "model"])
        self.assertEqual(done["steps"][0]["tool_calls"], ["calculator"])
        self.assertEqual(len(self.c.get("/v1/runs").json()), 1)

    def test_validation_errors_have_one_shape(self):
        for body in ({}, {"objective": ""}, {"objective": "x", "max_steps": 0}, {"objective": "x", "evil": 1}, {"objective": "x" * 5000}):
            r = self.c.post("/v1/runs", json=body)
            self.assertEqual(r.status_code, 422, body)
            self.assertEqual(set(r.json()["error"]), {"code", "message", "request_id"})
            self.assertEqual(r.json()["error"]["request_id"], r.headers["x-request-id"])
        r = self.c.get("/v1/runs/missing")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "not_found"))

    def test_request_id_is_propagated_and_sanitised(self):
        r = self.c.get("/healthz", headers={"X-Request-ID": "abc-123"})
        self.assertEqual(r.headers["x-request-id"], "abc-123")
        r = self.c.get("/healthz", headers={"X-Request-ID": "a b\r\nSet-Cookie: x"})
        self.assertNotIn("\n", r.headers["x-request-id"]); self.assertNotIn(" ", r.headers["x-request-id"])
        run = self.post_run()
        self.assertEqual(run["request_id"], self.c.get(f"/v1/runs/{run['id']}").json()["request_id"])

    def test_step_cap_enforced_by_server(self):
        run = self.post_run(max_steps=100)
        self.assertEqual(run["max_steps"], self.settings.max_steps_cap)

    def test_secret_pasted_in_objective_is_not_stored(self):
        run = self.post_run("compute 1 + 1 and my key is sk-" + "z" * 30)
        done = wait_status(self.c, run["id"], ("completed", "failed"))
        self.assertNotIn("zzzzzzzz", json.dumps(done) + json.dumps(self.c.get(f"/v1/runs/{run['id']}/events").json()))

    def test_eval_endpoint_runs_suite_and_persists(self):
        r = self.c.post("/v1/evals/run").json()
        self.assertEqual(r["aggregate"]["cases"], 13)
        self.assertEqual(self.c.get("/v1/evals").json()[0]["id"], r["id"])


class AuthTests(ApiBase):
    token = "s3cret-token"

    def test_v1_requires_bearer_but_health_does_not(self):
        self.assertEqual(self.c.get("/healthz").status_code, 200)
        self.assertEqual(self.c.get("/readyz").status_code, 200)
        self.assertEqual(self.c.post("/v1/runs", json={"objective": "x"}).status_code, 401)
        self.assertEqual(self.c.get("/v1/runs", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.c.post("/v1/runs", json={"objective": "compute 1 + 1"}, headers=self.h).status_code, 202)
        self.assertEqual(self.c.get("/v1/runs", headers=self.h).status_code, 200)
        self.assertEqual(self.c.get("/v1/runs/x/stream").status_code, 401)
        self.assertNotIn("s3cret-token", self.c.get("/readyz").text)


class StreamingTests(ApiBase):
    def test_event_vocabulary_and_order_over_sse(self):
        run = self.post_run()
        events = sse(self.c, f"/v1/runs/{run['id']}/stream")
        types = [e["event"] for e in events]
        self.assertEqual(types[0], "run_queued"); self.assertEqual(types[-1], "run_completed")
        for needed in ("run_started", "model_request", "model_response", "tool_requested", "policy", "tool_completed", "evaluation"):
            self.assertIn(needed, types)
        self.assertLess(types.index("tool_requested"), types.index("tool_completed"))
        self.assertLess(types.index("evaluation"), types.index("run_completed"))
        seqs = [int(e["id"]) for e in events]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertEqual(events[-1]["data"]["status"], "completed")
        tc = next(e for e in events if e["event"] == "tool_completed")["data"]
        self.assertTrue(tc["result_ok"]); self.assertGreaterEqual(tc["duration_ms"], 0)

    def test_resume_with_last_event_id_has_no_gaps_or_duplicates(self):
        run = self.post_run(); wait_status(self.c, run["id"], ("completed",))
        full = sse(self.c, f"/v1/runs/{run['id']}/stream")
        mid = full[3]["id"]
        resumed = sse(self.c, f"/v1/runs/{run['id']}/stream", headers={"Last-Event-ID": mid})
        self.assertEqual([e["id"] for e in resumed], [e["id"] for e in full[4:]])
        self.assertEqual([e["seq"] for e in self.c.get(f"/v1/runs/{run['id']}/events?after={mid}").json()], [int(e["id"]) for e in full[4:]])

    def test_unknown_run_stream_is_404(self):
        self.assertEqual(self.c.get("/v1/runs/nope/stream").status_code, 404)


class HiddenReasoningTests(ApiBase):
    provider = staticmethod(lambda: ScriptedModel([
        ModelResponse(content="PRIVATE REASONING: I should secretly do X", tool_calls=[ToolCall("c1", "calculator", {"expression": "2+2"})]),
        "The answer is 4."]))

    def test_model_text_that_accompanies_tool_calls_is_never_exposed(self):
        run = self.post_run("compute 2 + 2")
        done = wait_status(self.c, run["id"], ("completed",))
        blob = json.dumps(done) + json.dumps(self.c.get(f"/v1/runs/{run['id']}/events").json()) + json.dumps(sse(self.c, f"/v1/runs/{run['id']}/stream"))
        self.assertNotIn("PRIVATE REASONING", blob)
        self.assertEqual(done["final_answer"], "The answer is 4.")


class ApprovalTests(ApiBase):
    provider = staticmethod(lambda: ScriptedModel([
        ModelResponse(tool_calls=[ToolCall("w1", "write_file", {"path": "note.txt", "text": "hello"})]), "done"]))

    def _await_pending(self, run_id):
        end = time.time() + 5
        while time.time() < end:
            a = self.c.get(f"/v1/runs/{run_id}/approvals", headers=self.h).json()
            if a and a[0]["status"] == "pending":
                return a[0]
            time.sleep(0.02)
        self.fail("no pending approval")

    def test_write_tool_waits_for_human_and_then_runs(self):
        run = self.post_run("write a note")
        a = self._await_pending(run["id"])
        self.assertEqual((a["tool"], a["args"]["path"]), ("write_file", "note.txt"))
        self.assertEqual(wait_status(self.c, run["id"], ("awaiting_approval",))["status"], "awaiting_approval")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "note.txt")))
        r = self.c.post(f"/v1/runs/{run['id']}/approvals/{a['id']}", json={"approved": True}, headers=self.h)
        self.assertEqual(r.json()["status"], "approved")
        wait_status(self.c, run["id"], ("completed",))
        self.assertEqual(open(os.path.join(self.tmp.name, "note.txt")).read(), "hello")
        types = [e["type"] for e in self.c.get(f"/v1/runs/{run['id']}/events").json()]
        self.assertLess(types.index("approval_required"), types.index("approval_decision"))
        ev = next(e for e in self.c.get(f"/v1/runs/{run['id']}/events").json() if e["type"] == "approval_required")
        self.assertEqual(ev["payload"]["approval_id"], a["id"])

    def test_denied_write_does_not_happen_and_double_decision_conflicts(self):
        run = self.post_run("write a note")
        a = self._await_pending(run["id"])
        self.assertEqual(self.c.post(f"/v1/runs/{run['id']}/approvals/{a['id']}", json={"approved": False}).status_code, 200)
        again = self.c.post(f"/v1/runs/{run['id']}/approvals/{a['id']}", json={"approved": True})
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "already_decided"))
        wait_status(self.c, run["id"], ("completed",))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "note.txt")))

    def test_approval_of_another_runs_id_is_404(self):
        run = self.post_run("write a note"); a = self._await_pending(run["id"])
        other = self.store.create_run("x", 3)["id"]
        self.assertEqual(self.c.post(f"/v1/runs/{other}/approvals/{a['id']}", json={"approved": True}).status_code, 404)
        self.c.post(f"/v1/runs/{run['id']}/approvals/{a['id']}", json={"approved": False})


class ApprovalTimeoutTests(ApprovalTests):
    def setUp(self):
        super().setUp()
        self.settings = Settings(workspace=self.tmp.name, approval_timeout_s=0.3)
        self.c.__exit__(None, None, None)
        self.app = create_app(self.settings, self.store, manager=RunManager(self.settings, self.store, self.provider, poll_s=0.01))
        self.c = TestClient(self.app); self.c.__enter__()

    def test_undecided_approval_expires_as_denied(self):
        run = self.post_run("write a note")
        wait_status(self.c, run["id"], ("completed",))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "note.txt")))
        types = [e["type"] for e in self.c.get(f"/v1/runs/{run['id']}/events").json()]
        self.assertIn("approval_expired", types)
        self.assertEqual(self.store.list_approvals(run["id"])[0]["status"], "denied")

    # parent tests need a human; skip them here
    test_write_tool_waits_for_human_and_then_runs = None
    test_denied_write_does_not_happen_and_double_decision_conflicts = None
    test_approval_of_another_runs_id_is_404 = None


class _Blocker(ModelProvider):
    gate = threading.Event()

    def complete(self, messages, tools=None):
        self.gate.wait(5)
        return ModelResponse(content="ok")


class ConcurrencyTests(ApiBase):
    extra = {"max_concurrent_runs": 1}
    provider = staticmethod(lambda: _Blocker())

    def test_second_concurrent_run_gets_429_then_capacity_returns(self):
        _Blocker.gate.clear()
        first = self.post_run("one")
        r = self.c.post("/v1/runs", json={"objective": "two"})
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (429, "too_many_runs"))
        _Blocker.gate.set()
        wait_status(self.c, first["id"], ("completed",))
        end = time.time() + 3
        while self.app.state.manager.active and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(self.c.post("/v1/runs", json={"objective": "three"}).status_code, 202)


class RecoveryTests(unittest.TestCase):
    @unittest.skipUnless(HAVE, "service dependencies not installed")
    def test_runs_left_active_by_a_dead_process_are_marked_interrupted_on_start(self):
        store = MemoryStore(); rid = store.create_run("x", 3)["id"]; store.update_run(rid, status="running")
        with TestClient(create_app(Settings(), store)):
            pass
        self.assertEqual((store.get_run(rid)["status"], store.get_run(rid)["stop_reason"]), ("failed", "interrupted"))


if __name__ == "__main__":
    unittest.main()
