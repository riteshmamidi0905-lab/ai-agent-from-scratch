import time, unittest
from agent.loop import Agent
from agent.model import ModelResponse, ProviderError, RuleModel, ScriptedModel, ToolCall
from agent.reliability import Budget, EscalationRequired
from agent.security import Policy
from agent.tools import Tool, ToolRegistry, default_registry, safe_eval


def call(i, name, **args):
    return ModelResponse(tool_calls=[ToolCall(i, name, args)])


class ToolTests(unittest.TestCase):
    def test_safe_eval_blocks_code(self):
        self.assertEqual(safe_eval("2 + 3 * 4"), 14)
        for bad in ("__import__('os').system('x')", "open('f')", "2 ** 999", "[1]"):
            with self.assertRaises(Exception):
                safe_eval(bad)

    def test_registry_validates_and_never_raises(self):
        r = default_registry()
        self.assertTrue(r.execute("calculator", {"expression": "2*3"}).ok)
        self.assertEqual(r.execute("calculator", {}).kind, "validation")
        self.assertEqual(r.execute("calculator", {"expression": 5}).kind, "validation")
        self.assertEqual(r.execute("calculator", {"expression": "1", "x": 1}).kind, "validation")
        self.assertEqual(r.execute("nope", {}).kind, "validation")
        self.assertEqual(r.execute("calculator", {"expression": "1/0"}).kind, "tool_error")
        self.assertEqual(r.execute("convert_units", {"value": 1, "from": "km", "to": "kg"}).kind, "tool_error")

    def test_timeout(self):
        r = ToolRegistry()
        r.register(Tool("slow", "d", {"properties": {}}, lambda: time.sleep(1), timeout=0.05))
        self.assertEqual(r.execute("slow", {}).kind, "tool_timeout")

    def test_escalation_and_permission_kinds(self):
        r = ToolRegistry()
        def esc(): raise EscalationRequired("needs a human")
        def perm(): raise PermissionError("no")
        r.register(Tool("e", "d", {"properties": {}}, esc))
        r.register(Tool("p", "d", {"properties": {}}, perm))
        self.assertEqual(r.execute("e", {}).kind, "escalated")
        self.assertEqual(r.execute("p", {}).kind, "denied")


class LoopTests(unittest.TestCase):
    def agent(self, model, **kw):
        return Agent(model, kw.pop("registry", default_registry()), sleep=lambda s: None, **kw)

    def test_full_loop_with_rule_model(self):
        st = self.agent(RuleModel()).run("compute 6 x 7")
        self.assertEqual(st.status, "completed")
        self.assertIn("42", st.final_answer)
        self.assertEqual([s.kind for s in st.steps], ["model", "tool", "model"])
        self.assertGreater(st.usage.total, 0)

    def test_tool_error_is_fed_back_and_agent_recovers(self):
        m = ScriptedModel([call("a", "calculator", expression="1/0"), call("b", "calculator", expression="1/1"), "It is 1."])
        st = self.agent(m).run("divide")
        self.assertEqual(st.status, "completed")
        fed_back = m.calls[1][-1]
        self.assertEqual(fed_back.role, "tool")
        self.assertTrue(fed_back.content.startswith("ERROR"))

    def test_unknown_tool_and_bad_args_do_not_crash(self):
        m = ScriptedModel([call("a", "ghost", x=1), "done"])
        self.assertEqual(self.agent(m).run("x").status, "completed")

    def test_step_limit_stops_runaway(self):
        m = ScriptedModel([call(str(i), "calculator", expression=f"{i}+1") for i in range(50)])
        st = self.agent(m, budget=Budget(max_steps=4)).run("go")
        self.assertEqual((st.status, st.stop_reason), ("stopped", "step_limit"))

    def test_identical_call_loop_detected(self):
        m = ScriptedModel([call(str(i), "calculator", expression="1+1") for i in range(10)])
        st = self.agent(m).run("go")
        self.assertEqual(st.stop_reason, "loop_detected")

    def test_token_budget(self):
        m = ScriptedModel([call(str(i), "calculator", expression=f"{i}+2") for i in range(10)])
        st = self.agent(m, budget=Budget(max_steps=50, max_tokens=60)).run("a fairly long objective " * 10)
        self.assertEqual(st.stop_reason, "token_budget")

    def test_retry_then_success_and_trace(self):
        m = ScriptedModel([ProviderError("503", retryable=True), ProviderError("503", retryable=True), "ok"])
        a = self.agent(m)
        st = a.run("hi")
        self.assertEqual(st.status, "completed")
        self.assertEqual(sum(1 for e in a.tracer.events if e["type"] == "retry"), 2)

    def test_non_retryable_provider_error_fails_run(self):
        st = self.agent(ScriptedModel([ProviderError("bad key", retryable=False)])).run("hi")
        self.assertEqual((st.status, st.stop_reason), ("failed", "provider_error"))

    def test_duplicate_call_id_runs_tool_once(self):
        n = {"c": 0}
        r = ToolRegistry()
        def side(): n["c"] += 1; return "sent"
        r.register(Tool("send", "d", {"properties": {}}, side, level="read"))
        m = ScriptedModel([ModelResponse(tool_calls=[ToolCall("same", "send", {}), ToolCall("same", "send", {})]), "done"])
        self.agent(m, registry=r).run("x")
        self.assertEqual(n["c"], 1)

    def test_escalation_ends_run_as_escalated(self):
        r = ToolRegistry()
        def esc(): raise EscalationRequired("refund over limit")
        r.register(Tool("refund", "d", {"properties": {}}, esc))
        st = self.agent(ScriptedModel([call("a", "refund")]), registry=r).run("refund")
        self.assertEqual((st.status, st.stop_reason), ("escalated", "refund over limit"))

    def test_run_state_round_trips_json(self):
        from agent.state import RunState
        st = self.agent(RuleModel()).run("compute 2 x 2")
        back = RunState.from_json(st.to_json())
        self.assertEqual(back.final_answer, st.final_answer)
        self.assertEqual(len(back.steps), len(st.steps))


if __name__ == "__main__":
    unittest.main()
