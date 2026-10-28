# Model race leaderboard (test split)

| Model | Field F1 | W-2 | Pay stub | Bank | Invoice | Native F1 | Scan F1 | Review rate | p95 latency (s) | Tokens in/out per doc | Cost per 1K docs (USD) | Failed docs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemini:gemini-3.5-flash-lite | 1.00 | 0.98 | 1.00 | 1.00 | 1.00 | 1.00 | 0.98 | 0.02 | 26.8 | 317/341 | n/a | 0 |
| ollama:docintel-qwen1.5b-ft | 0.97 | 0.98 | 0.98 | 0.99 | 0.90 | 0.97 | 0.95 | 0.11 | 43.6 | 312/247 | 0.00 | 0 |
| ollama:qwen2.5:1.5b | 0.88 | 0.96 | 0.87 | 0.82 | 0.84 | 0.89 | 0.87 | 0.78 | 34.0 | 312/144 | 0.00 | 0 |
| rules-v1 | 0.38 | 0.45 | 0.19 | 0.67 | 0.11 | 0.42 | 0.37 | 1.00 | 6.4 | 0/0 | 0.00 | 0 |

50 test documents. Latency is wall-clock per document on the machine that ran the eval; cost is at paid list prices (free tiers cost $0).
