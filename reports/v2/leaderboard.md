# Model race leaderboard (test split)

| Model | Field F1 | W-2 | Pay stub | Bank | Invoice | Native F1 | Scan F1 | Review rate | p95 latency (s) | Tokens in/out per doc | Cost per 1K docs (USD) | Failed docs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ollama:docintel-qwen1.5b-ft-v2 | 0.94 | 0.89 | 0.97 | 0.92 | 0.99 | 0.99 | 0.82 | 0.26 | 438.7 | 1066/571 | 0.00 | 2 |
| gemini:gemini-3.5-flash-lite | 0.91 | 0.93 | 0.99 | 0.88 | 0.85 | 0.94 | 0.84 | 0.79 | 10.2 | 1131/1138 | n/a | 0 |
| ollama:qwen2.5-1.5b-8k | 0.65 | 0.25 | 0.96 | 0.90 | 0.74 | 0.71 | 0.72 | 1.00 | 196.4 | 1150/532 | 0.00 | 5 |
| rules-v1 | 0.17 | 0.04 | 0.43 | 0.19 | 0.14 | 0.11 | 0.33 | 0.92 | 3.8 | 0/0 | 0.00 | 0 |

55 test documents. Latency is wall-clock per document on the machine that ran the eval; cost is at paid list prices (free tiers cost $0).
