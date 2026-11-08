"""Hybrid passage search: BM25 always, plus embeddings when Ollama has nomic-embed-text.
The two rankings are merged with reciprocal rank fusion."""
from __future__ import annotations

import math
import os
import re
from collections import Counter

import httpx

from .reader import Passage

TOKEN = re.compile(r"[a-z0-9]+")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")


def tokens(s: str) -> list[str]:
    return TOKEN.findall(s.lower())


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.toks = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.toks)) / max(1, len(self.toks))
        df = Counter(t for d in self.toks for t in set(d))
        n = len(self.toks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in self.toks]

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        out = []
        for tf, d in zip(self.tf, self.toks, strict=True):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out


def embed(texts: list[str], timeout: float = 300) -> list[list[float]] | None:
    """Embeddings from local Ollama; None when it or the model is unavailable (BM25 alone then)."""
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        out = []
        for i in range(0, len(texts), 64):
            r = httpx.post(f"{base}/api/embed", json={"model": EMBED_MODEL, "input": texts[i:i + 64]}, timeout=timeout)
            r.raise_for_status()
            out += r.json()["embeddings"]
        return out
    except Exception:  # noqa: BLE001 - optional component
        return None


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=True))
    return num / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0)


class Index:
    def __init__(self, passages: list[Passage], vectors: list[list[float]] | None = None):
        self.passages = passages
        self.bm25 = BM25([p.text for p in passages])
        self.vectors = vectors

    @classmethod
    def build(cls, passages: list[Passage], use_embeddings: bool = True) -> Index:
        vecs = embed([f"search_document: {p.text}" for p in passages]) if use_embeddings and passages else None
        return cls(passages, vecs)

    def search(self, query: str, k: int = 6) -> list[Passage]:
        bm = self.bm25.scores(query)
        rankings = [sorted(range(len(self.passages)), key=lambda i: -bm[i])]
        if self.vectors:
            qv = embed([f"search_query: {query}"], timeout=10)  # a busy Ollama must not stall a question
            if qv:
                rankings.append(sorted(range(len(self.passages)), key=lambda i: -_cos(qv[0], self.vectors[i])))
        fused: Counter = Counter()
        for r in rankings:
            for rank, i in enumerate(r[:50]):
                fused[i] += 1 / (60 + rank)
        return [self.passages[i] for i, _ in fused.most_common(k)]
