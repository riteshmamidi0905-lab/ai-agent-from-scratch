# 05 · How do you evaluate an agent?

Evaluate the **run**, not just the final sentence. `evals.py` scores each run on checks that need no judge model:
- **completed** — reached the expected end state;
- **tool_correct** — exact expected tools, in order;
- **adherent** — required text present, forbidden text absent;
- **grounded** — every number in the answer appears in the objective or a tool output (catches invented figures);
- **latency, tokens, steps, failure kinds**.

Deterministic checks are cheap and CI-safe. They cannot judge fluency or reasoning quality, so for those you add a model-based judge or human review — and you measure the judge too.

**Honest limit of this repo's report:** `docs/eval-report.md` uses the offline `RuleModel`, so it measures the *runtime* (loop, tools, guards) and says nothing about any LLM. A real-model report needs a provider and a recorded run.

**Failure cases are cases too.** `expect_status="stopped"` lets a suite assert that a runaway agent *does* get stopped.
