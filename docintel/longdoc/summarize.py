"""Cited summary of a long document.

map:    each section (~SECTION_WORDS of consecutive passages) -> key facts, each with page + quote
check:  every fact goes through verify.check; failures are kept aside, never shown as fact
reduce: an overview written only from the verified facts; each sentence names the facts it
        uses, inherits their citations, and is checked again (numbers must be in the quotes)
"""
from __future__ import annotations

from collections.abc import Callable

from . import verify
from .llm import Chat
from .reader import LongDoc

SECTION_WORDS = 1800        # hosted models (large context)
LOCAL_SECTION_WORDS = 900   # local models: Ollama's default 4k-token window must hold section + prompt + reply

MAP_SYSTEM = """You read one section of a long document and list its most important facts.
Rules:
- Use ONLY the text given. Never add outside knowledge.
- Each fact is one self-contained sentence a reader can understand alone.
- For each fact copy an exact quote (8-40 words, word for word, from the text) that proves it,
  and the page number shown in [p N] before that text.
- Keep every number exactly as written in the quote.
Return JSON: {"facts": [{"fact": "...", "page": N, "quote": "..."}]} with 3 to 8 facts."""

REDUCE_SYSTEM = """You write an executive summary from numbered, verified facts about a document.
Rules:
- Use ONLY the facts given. Do not add anything else.
- Every sentence lists the ids of the facts it is based on.
- Copy numbers exactly as they appear in those facts.
Return JSON: {"overview": [{"sentence": "...", "facts": [ids]}], "key_points": [ids], "explain": "..."}
"overview": 4-8 sentences covering the whole document in order.
"key_points": the 5-10 most important fact ids.
"explain": two or three plain-language sentences saying what this document is and who it is for."""


def sections(doc: LongDoc, words: int = SECTION_WORDS) -> list[str]:
    out, buf, n = [], [], 0
    for p in doc.passages:
        buf.append(f"[p {p.page}] {p.text}")
        n += len(p.text.split())
        if n >= words:
            out.append("\n".join(buf))
            buf, n = [], 0
    if buf:
        out.append("\n".join(buf))
    return out


def summarize(doc: LongDoc, chat: Chat | None = None, progress: Callable[[int, int], None] | None = None) -> dict:
    chat = chat or Chat()
    facts, rejected = [], []
    secs = sections(doc, LOCAL_SECTION_WORDS if getattr(chat, "local", False) else SECTION_WORDS)
    for k, sec in enumerate(secs, 1):
        raw = chat.json(MAP_SYSTEM, f"Section {k} of {len(secs)}:\n\n{sec}")
        for f in raw.get("facts") or []:
            if not isinstance(f, dict) or not f.get("fact"):
                continue
            ok, why, cites = verify.check(str(f["fact"]), [f], doc.pages)
            item = {"text": str(f["fact"]).strip(), "citations": cites or [{"page": f.get("page"),
                                                                             "quote": f.get("quote")}]}
            if ok:
                item["id"] = len(facts) + 1
                facts.append(item)
            else:
                rejected.append(item | {"reason": why})
        if progress:
            progress(k, len(secs) + 1)
    overview, key_points, explain = [], [], None
    if facts:
        listing = "\n".join(f"{f['id']}. (p {f['citations'][0]['page']}) {f['text']}" for f in facts)
        raw = chat.json(REDUCE_SYSTEM, f"Document: {doc.meta.get('title')} ({doc.meta.get('pages')} pages)\n\n"
                                       f"Facts:\n{listing}")
        by_id = {f["id"]: f for f in facts}
        for s in raw.get("overview") or []:
            if not isinstance(s, dict) or not s.get("sentence"):
                continue
            used = [by_id[i] for i in _ids(s.get("facts")) if i in by_id]
            cites = [c for f in used for c in f["citations"]]
            ok, why, cites = verify.check(str(s["sentence"]), cites, doc.pages) if cites else (False, "no facts cited",
                                                                                                [])
            item = {"text": str(s["sentence"]).strip(), "citations": cites}
            (overview if ok else rejected).append(item if ok else item | {"reason": why})
        key_points = [by_id[i] for i in _ids(raw.get("key_points")) if i in by_id] or facts[:8]
        explain = raw.get("explain") if isinstance(raw.get("explain"), str) else None
    if progress:
        progress(len(secs) + 1, len(secs) + 1)
    return {"model": chat.name, "overview": overview, "key_points": key_points, "facts": facts,
            "explain": explain, "explain_note": "Plain-language description; not a factual claim, so not cited.",
            "rejected": rejected, "stats": {"sections": len(secs), "facts_verified": len(facts),
                                            "statements_rejected": len(rejected), "model_calls": chat.calls}}


def _ids(x) -> list[int]:
    out = []
    for i in x or []:
        try:
            out.append(int(i))
        except (TypeError, ValueError):
            pass
    return out
