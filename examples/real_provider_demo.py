"""Run the agent against a REAL model server (OpenAI-compatible: OpenAI, Ollama, vLLM, LM Studio...).
Credentials come ONLY from the environment; nothing is read from or written to files.

    export AGENT_BASE_URL=https://api.openai.com/v1     # or http://localhost:11434/v1 for Ollama
    export AGENT_MODEL=gpt-4o-mini
    export AGENT_API_KEY=...                            # not needed for most local servers
    python examples/real_provider_demo.py

It runs three small tasks and prints, for each: status, answer, tools used, tokens (provider-reported), and the evaluation_run checks.
These results come from whatever model you point it at — they are NOT part of CI and are not claimed anywhere in the docs."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agent.evals import evaluate_run
from agent.loop import Agent
from agent.model import OpenAICompatProvider
from agent.tools import default_registry

TASKS = ["What is 17 * 23? Use the calculator.", "Convert 12 km to miles using the tool.", "Say hello in one short sentence."]


def main() -> int:
    base, model, key = os.environ.get("AGENT_BASE_URL"), os.environ.get("AGENT_MODEL"), os.environ.get("AGENT_API_KEY", "")
    if not base or not model:
        print("Set AGENT_BASE_URL and AGENT_MODEL (and AGENT_API_KEY if the server needs one).")
        return 2
    for t in TASKS:
        a = Agent(OpenAICompatProvider(base, model, key), default_registry())
        st = a.run(t)
        ev = evaluate_run(st)
        print(f"\n> {t}\n  status={st.status}/{st.stop_reason}  answer={st.final_answer!r}\n  tools={[s.detail['tool'] for s in st.steps if s.kind == 'tool']}  tokens={st.usage.total}  eval={ev}")
        print("  trace summary:", a.tracer.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
