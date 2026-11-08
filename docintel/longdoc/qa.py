"""Answer a question about a long document, citing pages, or say the document does not say."""
from __future__ import annotations

from . import verify
from .index import Index
from .llm import Chat
from .reader import LongDoc

NOT_FOUND = "The document does not state this."
ON_TOPIC = 0.5  # share of the question's key words the cited quote must contain

SYSTEM = """You answer questions about a document using ONLY the passages given.
Rules:
- If the passages do not contain the answer, return {"answer": null, "citations": []}.
- Otherwise answer in 1-3 sentences, copying numbers exactly as written.
- Give 1-3 citations: the page number shown in [p N] and an exact quote (8-40 words, word for word)
  that proves the answer.
Return JSON: {"answer": "...", "citations": [{"page": N, "quote": "..."}]}"""


def ask(doc: LongDoc, index: Index, question: str, chat: Chat | None = None, k: int = 6) -> dict:
    chat = chat or Chat()
    hits = index.search(question, k)
    context = "\n\n".join(f"[p {p.page}] {p.text}" for p in hits)
    raw = chat.json(SYSTEM, f"Passages:\n{context}\n\nQuestion: {question}")
    pages = sorted({p.page for p in hits})
    answer = raw.get("answer")
    if not answer or not isinstance(answer, str):
        return {"question": question, "answer": NOT_FOUND, "found": False, "citations": [], "searched_pages": pages}
    ok, why, cites = verify.check(answer, raw.get("citations") or [], doc.pages)
    if ok and verify.support(question, [c["quote"] for c in cites]) < ON_TOPIC:
        # A real quote that answers a different question ("patents?" -> the headcount line)
        ok, why = False, "the quote does not answer the question"
    if not ok:
        return {"question": question, "answer": NOT_FOUND, "found": False, "citations": [], "searched_pages": pages,
                "withheld": {"answer": answer, "reason": why}}
    return {"question": question, "answer": answer.strip(), "found": True, "citations": cites,
            "searched_pages": pages}
