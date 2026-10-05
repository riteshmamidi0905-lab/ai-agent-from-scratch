# 01 · What is an agent? What is the loop?

**Definition I can defend:** an agent is a model in a loop that can call tools, where *software* — not the model — decides when the loop ends and what the model is allowed to do. Without the loop it is a chatbot; without the tools it is a loop that can only talk; without software control it is a liability.

**The loop** (`Agent.run`):
1. Build the message list (system prompt + recent history).
2. Call the model. It returns text, or tool calls, or both.
3. No tool calls → that text is the final answer. Done.
4. Tool calls → for each: check budget/loop, check policy, run the tool, wrap the output as untrusted data, append it as a `tool` message.
5. Go to 2.

**Why each part exists**
- *The model is stateless.* Every turn it gets the history again. That is why the loop owns history.
- *The model proposes, the runtime disposes.* Anything that must always hold (step limit, permissions, timeouts) cannot live in a prompt, because the model can ignore or be talked out of a prompt.
- *Termination is a runtime property.* The loop ends on: final answer, step limit, token budget, loop detection, provider failure, or escalation. Each has a named stop reason.

**Pitfall:** "agent" is often used for any LLM call with a tool. The distinguishing feature is the *iteration* — the output of an action feeds the next decision.
