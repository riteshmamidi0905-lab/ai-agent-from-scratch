# 06 · How do you secure tools?

Threat: text the model reads (documents, web pages, tool output, user input) can try to steer the actions it takes — **prompt injection**. There is no filter that fully prevents it, so assume some injections succeed and limit the damage:

1. **Least privilege** — `Policy.allow` allow-list; every tool declares `read | write | dangerous`; `max_level` caps it.
2. **Approval gates** — write/dangerous calls need a human callback; **no callback = deny** (fail closed).
3. **Path sandbox** — resolve the real path (so `..` and symlinks cannot escape) and require it to stay under the root.
4. **Untrusted-data boundary** — tool output is wrapped, secrets redacted, and instruction-like text flagged (or withheld). This *reduces* injection; it does not remove it.
5. **Safe tool implementations** — the calculator parses an AST; there is no `eval`.

The key test: `test_injected_tool_output_cannot_cause_write` — a poisoned document asks for a write; even if the model obeyed, the policy stops it. Defence in depth means the last layer holds when the earlier ones fail.
