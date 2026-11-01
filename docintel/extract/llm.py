"""LLM extractors for the model race (F7).

All share one prompt and parser; only the transport differs:
- OpenAICompatExtractor: Project 1 gateway, or Gemini / Groq directly via
  their OpenAI-compatible endpoints (free tiers).
- OpenAICompatExtractor("ollama", ...): small open models as GGUF on CPU via
  Ollama (the zero-shot small-model entrant, and later the fine-tuned one).
- HFLocalExtractor: the same through transformers, for GPU boxes (Kaggle/Colab);
  on CPU it is ~10x slower than llama.cpp.

The model sees OCR spans with numeric ids and returns, per field, the value,
a 0-1 confidence and the source span id, which is mapped back to a box.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from typing import Any

import httpx

from ..pipeline.ocr import Layout
from ..schemas import TABLE_COLUMNS, BBox, DocType, ExtractedField, field_types
from .base import coerce

SYSTEM = "You extract fields from financial documents. Reply with a single JSON object and nothing else."
PROMPT = """Document type: {doc_type}
Page text, one visual line per row; cells are `[span id] text` separated by ' | ' (table rows stay on one line):
{spans}

Return JSON mapping each field to {{"value": ..., "confidence": 0-1, "span": id|null}}.
- Scalar fields: value is a string. Copy names, IDs, dates and amounts exactly as printed, including separators
  (write "60.000", never the number 60.000); amounts in parentheses or with a leading minus are negative.
- Table fields: value is a list with one object per table row, in document order, across all pages; "span" is null.
Use null when a field is absent.
Scalar fields: {fields}
Table fields: {tables}"""


def describe_tables(types: dict[str, type]) -> str:
    cols = [f"{n} (columns: {', '.join(TABLE_COLUMNS[n])})" for n, t in types.items() if t is list]
    return "; ".join(cols) or "none"

MAX_ATTEMPTS = 7  # ~2 min of backoff in total
RETRY_STATUS = {429, 500, 502, 503, 504}
# Client-side pacing to stay inside free-tier requests-per-minute (override: <PROVIDER>_RPM).
DEFAULT_RPM = {"gemini": 5, "groq": 25}

PROVIDERS = {
    "gateway": (os.getenv("GATEWAY_URL", "http://localhost:8000/v1"), "GATEWAY_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "ollama": (os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1", None),  # local, no key
}


def parse_json(text: str) -> dict:
    """Tolerate code fences and chatter around the object."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        out = json.loads(m.group())
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


class LLMExtractor:
    name = "llm"

    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}

    def _chat(self, system: str, user: str) -> str:
        raise NotImplementedError

    def extract(self, layout: Layout, doc_type: DocType) -> dict[str, ExtractedField[Any]]:
        types = field_types(doc_type)
        raw = parse_json(self._chat(SYSTEM, PROMPT.format(
            doc_type=doc_type.value, spans=layout.render(),
            fields=", ".join(n for n, t in types.items() if t is not list), tables=describe_tables(types))))
        out: dict[str, ExtractedField[Any]] = {}
        for name, typ in types.items():
            item = raw.get(name)
            if not isinstance(item, dict):
                item = {"value": item, "confidence": 0.5 if item is not None else 0.0}
            sid = item.get("span")
            bbox, source = None, None
            if isinstance(sid, int) and 0 <= sid < len(layout.spans):
                s = layout.spans[sid]
                bbox = BBox(x0=s.bbox[0], y0=s.bbox[1], x1=s.bbox[2], y1=s.bbox[3], page=s.page)
                source = s.text
            try:
                conf = min(max(float(item.get("confidence") or 0.0), 0.0), 1.0)
            except (TypeError, ValueError):
                conf = 0.0
            if typ is list and isinstance(raw.get(name), list):  # model returned the rows directly
                item = {"value": raw[name], "confidence": 0.5}
            value = coerce(item.get("value"), typ, TABLE_COLUMNS.get(name))
            out[name] = ExtractedField[Any](value=value, confidence=conf if value is not None else 0.0, bbox=bbox,
                                            source_text=source)
        return out


class OpenAICompatExtractor(LLMExtractor):
    def __init__(self, provider: str, model: str, timeout: float = 90) -> None:
        super().__init__()
        base_url, key_env = PROVIDERS[provider]
        self.base_url, self.api_key = base_url.rstrip("/"), os.getenv(key_env, "") if key_env else "ollama"
        if not self.api_key:
            raise RuntimeError(f"{key_env} is not set")
        self.model, self.timeout = model, timeout
        self.name = f"{provider}:{model}"
        rpm = float(os.getenv(f"{provider.upper()}_RPM", DEFAULT_RPM.get(provider, 0)))
        self.min_interval = 60.0 / rpm if rpm > 0 else 0.0
        self._last_call = 0.0

    def _pace(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _chat(self, system: str, user: str) -> str:
        for attempt in range(MAX_ATTEMPTS):  # free tiers rate-limit and overload; back off with jitter
            self._pace()
            try:
                r = httpx.post(f"{self.base_url}/chat/completions", timeout=self.timeout,
                               headers={"Authorization": f"Bearer {self.api_key}"},
                               json={"model": self.model, "temperature": 0,
                                     "response_format": {"type": "json_object"},
                                     "messages": [{"role": "system", "content": system},
                                                  {"role": "user", "content": user}]})
            except httpx.TransportError as e:
                if attempt == MAX_ATTEMPTS - 1:
                    raise RuntimeError(f"{self.name} transport error: {e}") from None
                time.sleep(min(60, 2 ** (attempt + 1)) * random.uniform(0.8, 1.2))
                continue
            if r.status_code == 429 and "PerDay" in r.text:
                # Daily quota: retrying cannot succeed before the reset and every attempt burns quota.
                raise RuntimeError(f"{self.name} daily quota exhausted: {r.text[:200]}")
            if r.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                wait = r.headers.get("retry-after")
                time.sleep(float(wait) if wait else min(60, 2 ** (attempt + 1)) * random.uniform(0.8, 1.2))
                continue
            if r.is_error:  # plain exception: httpx errors don't pickle across worker processes
                raise RuntimeError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            u = body.get("usage") or {}
            self.last_usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)}
            return body["choices"][0]["message"]["content"]
        raise RuntimeError("unreachable")


class HFLocalExtractor(LLMExtractor):
    def __init__(self, model_id: str, max_new_tokens: int = 384) -> None:
        super().__init__()
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(int(os.getenv("TORCH_THREADS", os.cpu_count() or 4)))
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.float32)
        self.model.eval()
        self.max_new_tokens = max_new_tokens
        self.name = f"hf:{model_id.split('/')[-1]}"

    def _chat(self, system: str, user: str) -> str:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        enc = self.tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt", return_dict=True)
        n_in = enc["input_ids"].shape[1]
        with self.torch.inference_mode():
            out = self.model.generate(**enc, max_new_tokens=self.max_new_tokens, do_sample=False,
                                      pad_token_id=self.tok.eos_token_id)
        gen = out[0, n_in:]
        self.last_usage = {"input": int(n_in), "output": int(gen.shape[0])}
        return self.tok.decode(gen, skip_special_tokens=True)
