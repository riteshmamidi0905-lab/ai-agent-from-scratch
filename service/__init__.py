"""HTTP service around the agent runtime (V10–V12). The core `agent/` package knows nothing about HTTP, FastAPI or databases:
this layer only builds an `Agent`, runs it on a worker thread, and persists/streams the structured events the runtime already emits."""
