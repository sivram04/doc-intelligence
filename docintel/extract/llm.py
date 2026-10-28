"""LLM extractor via the Project 1 gateway (OpenAI-compatible chat API).

Gemini / Groq Llama / Qwen / the fine-tuned model behind Ollama are all just
different `model` strings on the gateway. The model gets the OCR spans with
numeric ids and must return, per field, the value, a 0-1 confidence and the
span id it came from, so we can attach the source box.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ..pipeline.ocr import Layout
from ..schemas import BBox, DocType, ExtractedField, field_types
from .base import coerce

PROMPT = """Extract fields from a {doc_type} document. Text spans are given as `[id] text`.
Return ONLY JSON: {{"<field>": {{"value": <string|number|null>, "confidence": <0-1>, "span": <id|null>}}}}
Fields: {fields}

{spans}"""


class LLMExtractor:
    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None):
        self.model = model or os.getenv("EXTRACT_MODEL", "gemini-2.5-flash")
        self.base_url = (base_url or os.getenv("GATEWAY_URL", "http://localhost:8000/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("GATEWAY_API_KEY", "")
        self.name = f"llm:{self.model}"

    def _chat(self, prompt: str) -> str:
        r = httpx.post(f"{self.base_url}/chat/completions", timeout=60,
                       headers={"Authorization": f"Bearer {self.api_key}"},
                       json={"model": self.model, "temperature": 0,
                             "response_format": {"type": "json_object"},
                             "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def extract(self, layout: Layout, doc_type: DocType) -> dict[str, ExtractedField[Any]]:
        types = field_types(doc_type)
        spans = "\n".join(f"[{i}] {s.text}" for i, s in enumerate(layout.spans))
        raw = json.loads(self._chat(PROMPT.format(doc_type=doc_type.value, fields=", ".join(types), spans=spans)))
        out: dict[str, ExtractedField[Any]] = {}
        for name, typ in types.items():
            item = raw.get(name) or {}
            sid = item.get("span")
            bbox = None
            if isinstance(sid, int) and 0 <= sid < len(layout.spans):
                s = layout.spans[sid]
                bbox = BBox(x0=s.bbox[0], y0=s.bbox[1], x1=s.bbox[2], y1=s.bbox[3], page=s.page)
            conf = float(item.get("confidence") or 0.0)
            out[name] = ExtractedField[Any](value=coerce(item.get("value"), typ),
                                            confidence=min(max(conf, 0.0), 1.0), bbox=bbox)
        return out
