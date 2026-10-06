"""The reproducible evaluation suite: successful AND failing runs. Shared by examples/run_eval.py and the service's POST /v1/evals/run so both report the same cases."""
from agent.evals import EvalCase

CASES = [
    EvalCase("arithmetic", "compute 6 x 7", ["calculator"], ["42"]),
    EvalCase("subtraction", "what is 100 - 58", ["calculator"], ["42"]),
    EvalCase("unit conversion", "convert 5 km to miles", ["convert_units"], ["3.10"]),
    EvalCase("division", "compute 10 / 4", ["calculator"], ["2.5"]),
    EvalCase("no tool available (honest refusal)", "tell me a joke", [], ["do not have a tool"]),
    EvalCase("tool failure is reported, not hidden", "compute 1 / 0", ["calculator"], ["could not finish"]),
]
