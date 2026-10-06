import json, os, tempfile, unittest
from agent.loop import Agent
from agent.memory import ConversationMemory
from agent.model import Message, ModelResponse, ProviderError, ScriptedModel, ToolCall
from agent.planner import PlanExecutor, build_plan
from agent.security import Policy
from agent.tools import Tool, add_file_tools, default_registry


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def call(i, name, **args):
    return ModelResponse(tool_calls=[ToolCall(i, name, args)])


class AuditFixTests(unittest.TestCase):
    def test_secrets_never_reach_trace_or_serialised_state(self):
        r = default_registry()
        r.register(Tool("leak", "d", {"properties": {}}, lambda: "token: abcdef123456 and sk-" + "a" * 24))
        a = Agent(ScriptedModel([call("a", "leak"), "done"]), r, sleep=lambda s: None)
        st = a.run("use the leaky tool, my key is sk-" + "b" * 24)
        blob = json.dumps(a.tracer.events) + st.to_json()
        for secret in ("abcdef123456", "sk-" + "a" * 24):
            self.assertNotIn(secret, blob)
        self.assertIn("[REDACTED]", blob)

    def test_objective_is_pinned_in_long_runs(self):
        m = ConversationMemory(max_tokens=60)
        m.add(Message("system", "s")); m.add(Message("user", "THE OBJECTIVE"))
        for i in range(20):
            m.add(Message("assistant", "thinking " * 5)); m.add(Message("user", f"filler {i} " * 4))
        self.assertIn("THE OBJECTIVE", [x.content for x in m.window()])

    def test_file_tools_confined_to_workspace_through_the_agent(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "secret.txt"); write(secret, "TOP SECRET")
            r = default_registry(); add_file_tools(r, d)
            write(os.path.join(d, "ok.txt"), "hello")
            m = ScriptedModel([call("1", "read_file", path="ok.txt"), call("2", "read_file", path="../" + os.path.basename(outside) + "/secret.txt"),
                               call("3", "read_file", path=secret), "done"])
            a = Agent(m, r, sleep=lambda s: None); st = a.run("read files")
            outs = [s.detail for s in st.steps if s.kind == "tool"]
            self.assertTrue(outs[0]["ok"]); self.assertFalse(outs[1]["ok"]); self.assertFalse(outs[2]["ok"])
            self.assertEqual(outs[1]["failure"], "denied")
            self.assertNotIn("TOP SECRET", json.dumps(a.tracer.events) + st.to_json())

    def test_write_file_needs_approval_and_emits_events(self):
        with tempfile.TemporaryDirectory() as d:
            r = default_registry(); add_file_tools(r, d)
            pol = Policy(approver=lambda n, args: True)
            a = Agent(ScriptedModel([call("1", "write_file", path="n/a.txt", text="x"), "ok"]), r, policy=pol, sleep=lambda s: None); a.run("write")
            types = [e["type"] for e in a.tracer.events]
            self.assertIn("approval_required", types); self.assertIn("approval_decision", types)
            self.assertTrue(os.path.exists(os.path.join(d, "n", "a.txt")))
            a2 = Agent(ScriptedModel([call("1", "write_file", path="b.txt", text="x"), "ok"]), r, sleep=lambda s: None); a2.run("write")
            self.assertFalse(os.path.exists(os.path.join(d, "b.txt")), "no approver configured => fail closed")

    def test_failed_replan_does_not_crash_executor(self):
        raw = {"tasks": [{"id": "a", "tool": "calculator", "args": {"expression": "1/0"}}]}
        ex = PlanExecutor(default_registry(), ScriptedModel(["garbage"] * 5), max_replans=1)
        done = ex.execute(build_plan(raw, default_registry()))
        self.assertEqual(done[0].status, "failed")
        self.assertTrue(any("replan failed" in l for l in ex.log))

    def test_tool_failures_are_recorded_in_state_errors(self):
        st = Agent(ScriptedModel([call("1", "calculator", expression="1/0"), "x"]), default_registry(), sleep=lambda s: None).run("q")
        self.assertEqual(st.errors[0]["kind"], "tool_error")


if __name__ == "__main__":
    unittest.main()


class V10CoreHookTests(unittest.TestCase):
    def test_listeners_receive_events_and_failures_are_isolated(self):
        from agent.trace import Tracer
        got = []
        def bad(ev): raise RuntimeError("listener bug")
        tr = Tracer(listeners=[bad, got.append])
        a = Agent(ScriptedModel([call("1", "calculator", expression="2+2"), "4"]), default_registry(), tracer=tr, sleep=lambda s: None)
        a.run("compute 2 + 2")
        types = [e["type"] for e in got]
        self.assertEqual(types[:3], ["model_request", "model_call", "tool_requested"])
        self.assertIn("tool_call", types)

    def test_evaluate_run(self):
        from agent.evals import evaluate_run
        a = Agent(ScriptedModel([call("1", "calculator", expression="2+2"), "The answer is 5."]), default_registry(), sleep=lambda s: None)
        ev = evaluate_run(a.run("compute 2 + 2"))
        self.assertTrue(ev["completed"]); self.assertFalse(ev["grounded"]); self.assertEqual(ev["ungrounded_numbers"], [5.0])
        self.assertEqual((ev["tool_calls"], ev["tool_failures"]), (1, 0))
