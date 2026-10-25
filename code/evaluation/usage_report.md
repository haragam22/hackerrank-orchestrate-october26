# Token usage and cost — run 'final'

Measured from the per-call usage log written during the run (`UsageLog`). Cost = input tokens × $1.00/1M + output tokens × $5.00/1M (AgentRouter `gpt-6.1-sol` console price); cache hits and the local Ollama fallback cost $0.

| Provider / model | Calls | Cache hits | Fallback calls | Input tokens | Output tokens | Total tokens | Cost |
|---|---|---|---|---|---|---|---|
| agentrouter / gpt-6.1-sol | 2,062 | 82 | 0 | 44,340,818 | 1,512,251 | 45,853,069 | $49.8154 |
| **Overall** | 2,062 | 82 | 0 | 44,340,818 | 1,512,251 | 45,853,069 | $49.8154 |

- Requests: 518
- Average tokens per request: 88,519 (85,600 in / 2,919 out)
- Average model calls per request: 3.98
- Estimated cost per request: $0.0962
## How the final run was produced
- One full pass over all 518 requests (`python code/main.py`, run `final`, 6 in parallel, ~5.4 h wall time,
  median 48 s per model call), then:
  - one retry pass of the 9 cases flagged `ok=false`: all replies came from the response cache (same prompts), $0, no change;
  - a re-run of the 10 cases whose evidence includes a screenshot or PDF, after fixing a bug that cut that text in the
    agent's tools (`--no-resume`; their new calls are included above);
  - case_55 and case_61 rebuilt from cached replies after adding the empty-verdict fallback ($0).
- All calls of all passes are in the table; cache hits are counted as calls but cost $0. No local fallback model call
  was needed.
