import json, os, tempfile, unittest
from agent.evals import EvalCase, run_suite, score, to_markdown
from agent.loop import Agent
from agent.model import ModelResponse, RuleModel, ScriptedModel, ToolCall
from agent.planner import PlanError, PlanExecutor, build_plan, topo_order, substitute
from agent.reliability import LoopDetector, call_signature, retry_call
from agent.model import ProviderError
from agent.tools import default_registry
from agent.trace import Tracer


class PlannerTests(unittest.TestCase):
    RAW = {"tasks": [{"id": "t2", "tool": "calculator", "args": {"expression": "$t1 * 2"}, "depends_on": ["t1"]},
                     {"id": "t1", "tool": "convert_units", "args": {"value": 5, "from": "km", "to": "mi"}}]}

    def test_dependency_order_and_substitution(self):
        tasks = build_plan(self.RAW, default_registry())
        self.assertEqual([t.id for t in topo_order(tasks)], ["t1", "t2"])
        ex = PlanExecutor(default_registry()); done = ex.execute(tasks)
        self.assertEqual([t.status for t in done], ["done", "done"])
        self.assertAlmostEqual(float(next(t for t in done if t.id == "t2").result), 6.213710, places=4)

    def test_invalid_plans_rejected_before_execution(self):
        reg = default_registry()
        with self.assertRaises(PlanError): build_plan({"tasks": [{"id": "a", "tool": "nope", "args": {}}]}, reg)
        with self.assertRaises(PlanError): build_plan({"tasks": [{"id": "a", "tool": "calculator", "args": {}, "depends_on": ["x"]}]}, reg)
        with self.assertRaises(PlanError): build_plan({"tasks": [{"id": "a", "tool": "calculator", "args": {}, "depends_on": ["b"]},
                                                              {"id": "b", "tool": "calculator", "args": {}, "depends_on": ["a"]}]}, reg)
        with self.assertRaises(PlanError): build_plan({"tasks": [{"id": "a", "tool": "calculator", "args": {}}, {"id": "a", "tool": "calculator", "args": {}}]}, reg)

    def test_failure_skips_dependents(self):
        raw = {"tasks": [{"id": "a", "tool": "calculator", "args": {"expression": "1/0"}}, {"id": "b", "tool": "calculator", "args": {"expression": "$a + 1"}, "depends_on": ["a"]}]}
        done = PlanExecutor(default_registry()).execute(build_plan(raw, default_registry()))
        self.assertEqual([t.status for t in done], ["failed", "skipped"])

    def test_replan_after_failure(self):
        raw = {"tasks": [{"id": "a", "tool": "calculator", "args": {"expression": "1/0"}}]}
        fixed = json.dumps({"tasks": [{"id": "a2", "tool": "calculator", "args": {"expression": "10/2"}}]})
        ex = PlanExecutor(default_registry(), ScriptedModel([fixed]), max_replans=1)
        done = ex.execute(build_plan(raw, default_registry()))
        self.assertEqual(done[-1].result, "5.0")
        self.assertIn("replanning after a", ex.log)

    def test_substitute(self):
        self.assertEqual(substitute({"v": "$t1"}, {"t1": "3.5"}), {"v": 3.5})
        self.assertEqual(substitute({"e": "$t1 * 2"}, {"t1": "3"}), {"e": "3 * 2"})


class ReliabilityTests(unittest.TestCase):
    def test_retry_only_retryable_with_backoff(self):
        waits, n = [], {"c": 0}
        def flaky():
            n["c"] += 1
            if n["c"] < 3: raise ProviderError("x", retryable=True)
            return "ok"
        self.assertEqual(retry_call(flaky, 3, 0.1, waits.append), "ok")
        self.assertEqual(waits, [0.1, 0.2])
        with self.assertRaises(ProviderError):
            retry_call(lambda: (_ for _ in ()).throw(ProviderError("bad", retryable=False)), 3, 0, lambda s: None)

    def test_loop_detector_and_idempotency_key(self):
        d = LoopDetector(3)
        self.assertEqual([d.observe("a", {"x": 1}) for _ in range(3)], [False, False, True])
        d2 = LoopDetector(3)
        self.assertFalse(any(d2.observe("a", {"x": i}) for i in range(5)))
        self.assertEqual(call_signature("t", {"b": 1, "a": 2}), call_signature("t", {"a": 2, "b": 1}))


class TraceEvalTests(unittest.TestCase):
    def test_trace_events_and_summary_and_sink(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.jsonl")
            a = Agent(RuleModel(), default_registry(), tracer=Tracer(sink=path), sleep=lambda s: None)
            a.run("compute 3 x 3")
            s = a.tracer.summary()
            self.assertEqual((s["model_calls"], s["tool_calls"]), (2, 1))
            self.assertGreater(s["prompt_tokens"], 0)
            with open(path) as fh:
                lines = [json.loads(l) for l in fh]
            self.assertEqual(len(lines), len(a.tracer.events))
            self.assertTrue(all("run" in l and "t" in l for l in lines))

    def make_agent(self):
        return Agent(RuleModel(), default_registry(), sleep=lambda s: None)

    def test_eval_suite_scores_correctly(self):
        cases = [EvalCase("math", "compute 6 x 7", ["calculator"], ["42"]),
                 EvalCase("units", "convert 5 km to miles", ["convert_units"], ["3.10"]),
                 EvalCase("no tool", "tell me a joke", [], ["do not have a tool"]),
                 EvalCase("wrong expectation", "compute 1 + 1", ["convert_units"], [])]
        rep = run_suite(self.make_agent, cases)
        rows = {r["case"]: r for r in rep["rows"]}
        self.assertTrue(rows["math"]["completed"] and rows["math"]["tool_correct"] and rows["math"]["adherent"] and rows["math"]["grounded"])
        self.assertTrue(rows["units"]["adherent"])
        self.assertFalse(rows["wrong expectation"]["tool_correct"])
        self.assertAlmostEqual(rep["aggregate"]["tool_correctness"], 0.75)
        self.assertIn("| completion_rate |", to_markdown(rep))

    def test_groundedness_catches_invented_numbers(self):
        m = ScriptedModel([ModelResponse(tool_calls=[ToolCall("a", "calculator", {"expression": "6*7"})]), "The answer is 43."])
        a = Agent(m, default_registry(), sleep=lambda s: None)
        st = a.run("compute 6 times 7")
        r = score(EvalCase("g", "compute 6 times 7", ["calculator"]), st, 1.0)
        self.assertFalse(r["grounded"]); self.assertEqual(r["ungrounded_numbers"], [43.0])

    def test_failure_cases_are_scorable(self):
        m = ScriptedModel([ModelResponse(tool_calls=[ToolCall(str(i), "calculator", {"expression": "1+1"})]) for i in range(5)])
        st = Agent(m, default_registry(), sleep=lambda s: None).run("loop")
        r = score(EvalCase("loop", "loop", expect_status="stopped"), st, 1.0)
        self.assertTrue(r["completed"]); self.assertEqual(r["stop_reason"], "loop_detected")


if __name__ == "__main__":
    unittest.main()
