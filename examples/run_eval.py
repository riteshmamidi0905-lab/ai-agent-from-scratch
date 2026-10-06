"""Run the agent eval suite against the deterministic offline RuleModel and print/write a report.
The numbers describe the RUNTIME (loop, tools, guards) with a rule-based stand-in for the model. They say nothing about any LLM."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent.evals import run_suite, to_markdown
from agent.loop import Agent
from agent.model import RuleModel
from agent.tools import default_registry

from agent.eval_cases import CASES

if __name__ == "__main__":
    rep = run_suite(lambda: Agent(RuleModel(), default_registry(), sleep=lambda s: None), CASES)
    md = "# Eval report — deterministic, offline (RuleModel + scripted misbehaving models). Measures the RUNTIME, not any LLM.\n\n" + to_markdown(rep) + "\n"
    out = os.path.join(os.path.dirname(__file__), "..", "docs", "eval-report.md")
    open(out, "w").write(md)
    print(md)
