# Eval report (offline RuleModel — measures the runtime, not an LLM)

| metric | value |
|---|---|
| cases | 6 |
| completion_rate | 1.0 |
| tool_correctness | 1.0 |
| instruction_adherence | 1.0 |
| groundedness | 1.0 |
| latency_ms_mean | 0.111 |
| latency_ms_p95 | 0.22 |
| tokens_total | 755 |

| case | status | completed | tools ok | adherent | grounded | steps | tokens |
|---|---|---|---|---|---|---|---|
| arithmetic | completed | True | True | True | True | 2 | 135 |
| subtraction | completed | True | True | True | True | 2 | 136 |
| unit conversion | completed | True | True | True | True | 2 | 146 |
| division | completed | True | True | True | True | 2 | 135 |
| no tool available | completed | True | True | True | True | 1 | 58 |
| tool failure is reported | completed | True | True | True | True | 2 | 145 |
