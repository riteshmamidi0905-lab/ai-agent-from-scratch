"""Run the agent eval suite against the deterministic offline RuleModel and print/write a report.
The numbers describe the RUNTIME (loop, tools, guards) with a rule-based stand-in for the model. They say nothing about any LLM."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent.evals import EvalCase, run_suite, to_markdown
from agent.loop import Agent
from agent.model import RuleModel
from agent.tools import default_registry

CASES = [
    EvalCase("arithmetic", "compute 6 x 7", ["calculator"], ["42"]),
    EvalCase("subtraction", "what is 100 - 58", ["calculator"], ["42"]),
    EvalCase("unit conversion", "convert 5 km to miles", ["convert_units"], ["3.10"]),
    EvalCase("division", "compute 10 / 4", ["calculator"], ["2.5"]),
    EvalCase("no tool available", "tell me a joke", [], ["do not have a tool"]),
    EvalCase("tool failure is reported", "compute 1 / 0", ["calculator"], ["could not finish"]),
]

if __name__ == "__main__":
    rep = run_suite(lambda: Agent(RuleModel(), default_registry(), sleep=lambda s: None), CASES)
    md = "# Eval report (offline RuleModel — measures the runtime, not an LLM)\n\n" + to_markdown(rep) + "\n"
    out = os.path.join(os.path.dirname(__file__), "..", "docs", "eval-report.md")
    open(out, "w").write(md)
    print(md)
