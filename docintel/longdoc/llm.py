"""JSON chat with the configured model. Same providers as extraction (Ollama, Gemini, Groq,
gateway), but a general instruct model: the fine-tuned extractor only knows invoices.

SUMMARY_MODEL=ollama:qwen2.5:7b      local (better: 7b; works: 1.5b)
SUMMARY_MODEL=gemini:gemini-3.5-flash-lite   free hosted tier
Default: Gemini when GEMINI_API_KEY is set, otherwise local qwen2.5:1.5b.
"""
from __future__ import annotations

import os

from ..extract.llm import OpenAICompatExtractor, parse_json


def default_model() -> str:
    if os.getenv("SUMMARY_MODEL"):
        return os.environ["SUMMARY_MODEL"]
    return "gemini:gemini-3.5-flash-lite" if os.getenv("GEMINI_API_KEY") else "ollama:qwen2.5:1.5b"


class Chat:
    def __init__(self, spec: str | None = None):
        spec = spec or default_model()
        provider, _, model = spec.partition(":")
        self.client = OpenAICompatExtractor(provider, model)
        # A small local model whose context overflows can write until the timeout; cap every reply.
        self.client.max_tokens = int(os.getenv("SUMMARY_MAX_TOKENS", "1200"))
        self.local = provider == "ollama"
        self.name = spec
        self.calls = 0

    def json(self, system: str, user: str) -> dict:
        self.calls += 1
        try:
            return parse_json(self.client._chat(system, user))
        except ValueError:
            return {}
