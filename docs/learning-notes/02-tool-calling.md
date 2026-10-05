# 02 · How does tool calling work?

1. You describe each tool to the model as a name, a description and a JSON-schema for arguments (`Tool.spec`).
2. The model does not execute anything. It returns a *request*: `ToolCall(id, name, args)`.
3. The runtime validates `args` against the schema (`validate` in `structured.py`), runs the function, and returns a `ToolResult`.
4. The result is appended as a `tool` message tied to the call `id`; the next model call sees it.

**Why validate?** The model's arguments are untrusted input, exactly like a form submission. Wrong type, missing field, extra field, enum violation → a `validation` failure the model can read and fix, not a crash.

**Why `ToolResult` instead of exceptions?** Failure is a normal outcome of an agent step. Turning it into data (`ok`, `error`, `kind`) lets the model recover and the tracer classify it.

**Structured output** is the same mechanism for answers: ask for JSON, parse, validate, and on failure tell the model precisely what was wrong and retry a bounded number of times (`generate_structured`).

**Real providers** differ in wire format; `ModelProvider.complete` hides that. `OpenAICompatProvider` shows the shape (tested against a local HTTP server, not a paid API).
