# 07 · LangGraph? MCP?

(Concepts only for now; the hands-on comparison is a planned milestone — nothing here is measured.)

**LangGraph / graph frameworks** model an agent as nodes and edges with checkpointed state. Reach for them when you need: durable long-running workflows that resume after a crash, branching/parallel flows, human-in-the-loop pauses across processes, or a team that benefits from a shared vocabulary and tooling. Stay with a small custom loop when the flow is a simple tool loop, you need total control over guards and tracing, or the dependency and abstraction cost outweighs the benefit. The honest trade: a framework gives persistence and structure; you give up some transparency and pay with its concepts.

**MCP (Model Context Protocol)** standardises how an agent discovers and calls tools/resources exposed by *other* programs, so a tool is written once and any MCP-capable client can use it, instead of N×M custom integrations. It solves interoperability, not safety: an MCP tool is still untrusted input and output, so the policy/approval/sandbox layers here still apply.
