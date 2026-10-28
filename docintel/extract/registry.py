"""Build extractors from a spec string, e.g. `rules`, `hf:Qwen/Qwen2.5-1.5B-Instruct`,
`ollama:qwen2.5:1.5b`, `gemini:gemini-3.8-flash`, `groq:llama-3.3-70b-versatile`, `gateway:auto`."""
from __future__ import annotations

from functools import lru_cache

from .base import Extractor


@lru_cache(maxsize=4)
def make_extractor(spec: str = "rules") -> Extractor:
    kind, _, model = spec.partition(":")
    if kind == "rules":
        from .rules import RulesExtractor
        return RulesExtractor()
    if kind == "hf":
        from .llm import HFLocalExtractor
        return HFLocalExtractor(model)
    if kind in ("gateway", "gemini", "groq", "ollama"):
        from .llm import OpenAICompatExtractor
        return OpenAICompatExtractor(kind, model)
    raise ValueError(f"Unknown extractor spec {spec!r}")
