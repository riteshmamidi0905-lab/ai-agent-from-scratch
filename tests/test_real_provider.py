"""Optional integration test against a real model. SKIPPED unless explicitly enabled, so CI never depends on a paid API:

    AGENT_REAL_PROVIDER_TEST=1 AGENT_BASE_URL=... AGENT_MODEL=... AGENT_API_KEY=... python -m unittest tests.test_real_provider

Assertions are deliberately about the RUNTIME's behaviour (a run reaches a terminal state, usage is reported, a tool the model chose was
executed or the model answered directly) — not about model quality, which varies."""
import os, unittest

ENABLED = os.environ.get("AGENT_REAL_PROVIDER_TEST") == "1" and os.environ.get("AGENT_BASE_URL") and os.environ.get("AGENT_MODEL")


@unittest.skipUnless(ENABLED, "set AGENT_REAL_PROVIDER_TEST=1 plus AGENT_BASE_URL and AGENT_MODEL to run against a real model")
class RealProviderTests(unittest.TestCase):
    def test_real_model_completes_a_tool_task_through_the_runtime(self):
        from agent.evals import evaluate_run
        from agent.loop import Agent
        from agent.model import OpenAICompatProvider
        from agent.reliability import Budget
        from agent.tools import default_registry
        a = Agent(OpenAICompatProvider(os.environ["AGENT_BASE_URL"], os.environ["AGENT_MODEL"], os.environ.get("AGENT_API_KEY", ""), timeout=60),
                  default_registry(), budget=Budget(max_steps=6, max_tokens=8000))
        st = a.run("What is 17 * 23? Use the calculator tool and state the number.")
        self.assertIn(st.status, ("completed", "stopped", "failed"))      # always terminal, never hangs
        self.assertGreater(st.usage.total, 0)
        ev = evaluate_run(st)
        if st.status == "completed":
            self.assertTrue(ev["grounded"] or ev["tool_calls"] == 0)


if __name__ == "__main__":
    unittest.main()
