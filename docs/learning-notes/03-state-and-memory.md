# 03 · Where does state live? What is memory?

**State** lives in `RunState`: status, ordered steps, tool results, errors, token usage. It is plain data, JSON round-trippable. Because the model is stateless and the loop is a function of this state, a run can be saved, inspected, replayed and scored.

**Memory is four different things:**
| Kind | Lifetime | What it is | Class |
|---|---|---|---|
| Conversation | one session | recent messages re-sent each turn; bounded by a token budget, system prompt kept, never starts on an orphan tool message | `ConversationMemory` |
| Working | one run | scratchpad of intermediate values | `WorkingMemory` |
| Persistent | across runs | explicit facts the agent chose to store | `PersistentMemory` (atomic JSON file) |
| Semantic | across runs | retrieve by similarity, not by key | `SemanticMemory` (hashed embeddings; offline stand-in for a real embedding model) |

**Pitfalls:** treating "the context window" as memory (it is only the conversation); storing everything (noise, privacy, cost); semantic retrieval returning confident but irrelevant text — hence a `min_score` threshold and returning nothing when nothing is close.
