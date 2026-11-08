"""Checks that keep made-up statements out of summaries and answers.

A statement passes only if
  1. its quote is on the cited page (exact after normalising spaces, quotes and dashes,
     or a near match of at least QUOTE_MATCH for OCR noise), and
  2. every number in the statement also appears in its quote(s), and
  3. enough of its content words appear in the quote(s) that the quote is about it.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

QUOTE_MATCH = 0.9
MIN_SUPPORT = 0.5
NUM_RE = re.compile(r"\d[\d,.]*\d|\d")
WORD_RE = re.compile(r"[a-z][a-z'-]{3,}")
STOP = set("""about above after again against also among because been before being below between both could does
doing down during each from further have having here into itself just more most other over same should some such
than that their them then there these they this those through under until very were what when where which while
with would your will shall total amount amounts company document report page section""".split())


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[–—−]", "-", s)
    return " ".join(s.split())


def numbers(s: str) -> set[str]:
    """Numbers as digit strings with a decimal point kept: '1,234.50' -> '1234.5', '12%' -> '12'."""
    out = set()
    for m in NUM_RE.findall(s):
        x = m.replace(",", "")
        if x.count(".") > 1:  # 1.234.567 thousands dots
            x = x.replace(".", "")
        if "." in x:
            x = x.rstrip("0").rstrip(".")
        out.add(x.lstrip("0") or "0")
    return out


def find_quote(quote: str, page_text: str) -> float:
    """1.0 when the quote is on the page, else the best fuzzy match score over windows of its length."""
    q, t = norm(quote), norm(page_text)
    if not q:
        return 0.0
    if q in t:
        return 1.0
    qw, tw = q.split(), t.split()
    n = len(qw)
    if n == 0 or not tw:
        return 0.0
    best = 0.0
    for i in range(0, max(1, len(tw) - n + 1)):
        r = SequenceMatcher(None, q, " ".join(tw[i:i + n])).ratio()
        best = max(best, r)
        if best >= 0.99:
            break
    return best


def _stem(w: str) -> str:
    return w[:5]  # crude but language-agnostic: employed/employees, increase/increased


def support(statement: str, quotes: list[str]) -> float:
    """Share of the statement's content words whose stem appears in the quotes."""
    words = {w for w in WORD_RE.findall(norm(statement)) if w not in STOP}
    if not words:
        return 1.0
    stems = {_stem(w) for w in WORD_RE.findall(norm(" ".join(quotes)))}
    return sum(_stem(w) in stems for w in words) / len(words)


def check(statement: str, citations: list[dict], pages: list[str]) -> tuple[bool, str, list[dict]]:
    """(ok, reason, citations that were found). Citations: [{"page": int, "quote": str}]."""
    good = []
    for c in citations or []:
        try:
            p = int(c.get("page"))
        except (TypeError, ValueError):
            continue
        quote = str(c.get("quote") or "").strip()
        if 1 <= p <= len(pages) and len(quote.split()) >= 3:
            score = find_quote(quote, pages[p - 1])
            if score < QUOTE_MATCH:  # models sometimes cite the next or previous page
                for q in (p - 1, p + 1):
                    if 1 <= q <= len(pages) and find_quote(quote, pages[q - 1]) >= QUOTE_MATCH:
                        p, score = q, 1.0
                        break
            if score >= QUOTE_MATCH:
                good.append({"page": p, "quote": quote})
    if not good:
        return False, "quote not found on the cited page", []
    quotes = [g["quote"] for g in good]
    missing = numbers(statement) - set().union(*(numbers(q) for q in quotes))
    if missing:
        return False, f"number(s) not in the quote: {', '.join(sorted(missing))}", good
    if support(statement, quotes) < MIN_SUPPORT:
        return False, "the quote does not support the statement", good
    return True, "verified", good
