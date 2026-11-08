"""Long documents (reports, contracts, filings): summarise, explain and answer questions,
with a page number and an exact quote behind every statement.

The rule is simple: nothing the model writes reaches the user unless it is backed by a
quote that is really on the cited page, and every number it states appears in that
quote. Statements that fail are dropped and listed separately, never shown as fact.

    reader     PDF/scan -> passages with page numbers (same OCR/layout as invoices)
    index      hybrid search: BM25 plus optional embeddings (Ollama nomic-embed-text)
    verify     quote and number checks against the cited page
    summarize  map (facts per section) -> reduce (overview), all cited
    qa         answer a question from retrieved passages, or say it is not in the document
"""
