"""Virtual cost per 1K documents (N1/Phase 3 leaderboard).

Free tiers cost $0 in practice; the leaderboard reports what the same volume
would cost at paid list prices so models can be compared. Prices are USD per
1M tokens (input, output). Fill API entries from the provider pricing pages
on the date you run the race; None means "not priced" and reports null.
"""
from __future__ import annotations

PRICES: dict[str, tuple[float, float] | None] = {
    "rules": (0.0, 0.0),
    "hf": (0.0, 0.0),
    "ollama": (0.0, 0.0),  # local CPU; compute cost is in latency, not tokens
    "gemini:gemini-2.5-flash": None,
    "groq:llama-3.3-70b-versatile": None,
    "groq:qwen/qwen3-32b": None,
}


def cost_per_1k_docs(spec: str, tokens_in: float, tokens_out: float) -> float | None:
    price = PRICES.get(spec, PRICES.get(spec.split(":")[0]))
    if price is None:
        return None
    return round(1000 * (tokens_in * price[0] + tokens_out * price[1]) / 1e6, 4)
