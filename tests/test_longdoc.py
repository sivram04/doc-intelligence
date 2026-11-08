"""Long-document feature: reading, search, and the checks that keep unsupported statements out."""
import json

import pytest

from docintel.longdoc import verify
from docintel.longdoc.index import Index
from docintel.longdoc.qa import NOT_FOUND, ask
from docintel.longdoc.reader import read
from docintel.longdoc.summarize import summarize
from docintel.synth.report import generate


class FakeChat:
    """Returns canned JSON, so tests check our logic, not a model."""

    name = "fake"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def json(self, system, user):
        self.calls += 1
        r = self.replies.pop(0) if self.replies else {}
        return r(user) if callable(r) else r


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("rep")
    gt = generate(out, 1, 20, 5, scan=False)[0]
    doc = read(out / gt["file"])
    return gt, doc, Index.build(doc.passages, use_embeddings=False)


def fact(gt, key):
    return next(f for f in gt["facts"] if f["key"] == key)


def test_reader_keeps_pages_and_metadata(report):
    gt, doc, _ = report
    assert doc.meta["pages"] == gt["pages"] == len(doc.pages)
    assert gt["company"] in doc.meta["title"]
    f = fact(gt, "revenue")
    assert f["answer"] in doc.pages[f["page"] - 1]


def test_search_finds_the_page(report):
    gt, _, index = report
    hits = 0
    for f in gt["facts"]:
        hits += f["page"] in [p.page for p in index.search(f["q"], 6)]
    assert hits >= len(gt["facts"]) - 1


def test_check_rejects_wrong_page_wrong_number_and_invented_quote(report):
    gt, doc, _ = report
    f = fact(gt, "revenue")
    s = f["sentence"]
    assert verify.check(s, [{"page": f["page"], "quote": s}], doc.pages)[0]
    far = (f["page"] + 5) % len(doc.pages) + 1
    assert not verify.check(s, [{"page": far, "quote": s}], doc.pages)[0]
    assert not verify.check(s.replace(f["answer"], "123.4"), [{"page": f["page"], "quote": s}], doc.pages)[0]
    assert not verify.check(s, [{"page": f["page"], "quote": "Revenue tripled thanks to a new product line"}],
                            doc.pages)[0]


def test_answer_shown_only_with_a_real_quote(report):
    gt, doc, index = report
    f = fact(gt, "dividend")
    good = {"answer": f"The board recommends ${f['answer']} per share.",
            "citations": [{"page": f["page"], "quote": f["sentence"]}]}
    r = ask(doc, index, f["q"], FakeChat([good]))
    assert r["found"] and r["citations"][0]["page"] == f["page"]
    made_up = {"answer": "The board recommends $9.99 per share.",
               "citations": [{"page": f["page"], "quote": f["sentence"]}]}
    r = ask(doc, index, f["q"], FakeChat([made_up]))
    assert not r["found"] and r["answer"] == NOT_FOUND and "9.99" in r["withheld"]["reason"]
    assert ask(doc, index, "What was the market share in Brazil?", FakeChat([{"answer": None}]))["answer"] == NOT_FOUND


def test_summary_keeps_only_verified_statements(report):
    gt, doc, _ = report
    f1, f2 = fact(gt, "revenue"), fact(gt, "staff")
    facts = {"facts": [{"fact": f1["sentence"], "page": f1["page"], "quote": f1["sentence"]},
                       {"fact": f2["sentence"], "page": f2["page"], "quote": f2["sentence"]},
                       {"fact": "Profits doubled to $1,000 million.", "page": 2, "quote": "profits doubled"}]}
    n_sections = len(__import__("docintel.longdoc.summarize", fromlist=["sections"]).sections(doc))
    reduce = {"overview": [{"sentence": f1["sentence"], "facts": [1]},
                           {"sentence": "Revenue was $5 billion.", "facts": [1]}],
              "key_points": [1, 2], "explain": "An annual report."}
    s = summarize(doc, FakeChat([facts] + [{"facts": []}] * (n_sections - 1) + [reduce]))
    assert [f["text"] for f in s["facts"]] == [f1["sentence"], f2["sentence"]]
    assert [o["text"] for o in s["overview"]] == [f1["sentence"]]
    assert {r["text"] for r in s["rejected"]} == {"Profits doubled to $1,000 million.", "Revenue was $5 billion."}
    json.dumps(s)


def test_numbers_normalise():
    assert verify.numbers("$1,234.50 and 12% in 2024") == {"1234.5", "12", "2024"}
    assert verify.numbers("1.234.567") == {"1234567"}


def test_real_quote_that_answers_another_question_is_withheld(report):
    """Found in testing: 'How many patents?' answered with the (real, correctly cited) headcount line."""
    gt, doc, index = report
    f = fact(gt, "staff")
    off_topic = {"answer": f["answer"], "citations": [{"page": f["page"], "quote": f["sentence"]}]}
    r = ask(doc, index, "How many patents did the company file?", FakeChat([off_topic]))
    assert not r["found"] and r["withheld"]["reason"] == "the quote does not answer the question"
