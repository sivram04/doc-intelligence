"""Browser tests: the real app in a real browser (Playwright), clicked the way a visitor would.

Models are replaced by stand-ins that answer from the documents' known values, so the tests are
fast and exact; the models themselves are measured by the benchmarks in scripts/.
Uses Microsoft Edge or Chrome if installed, Playwright's Chromium otherwise; skipped without a browser.

    pytest tests/test_ui.py
"""
import json
import socket
import threading
import time
import zipfile
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from docintel.schemas import ExtractedField
from docintel.synth.intl_invoices import generate as make_invoices
from docintel.synth.report import generate as make_reports
from tests.test_batches import _app


class KnownAnswers:
    """Extractor stand-in: returns the ground truth of whichever generated invoice is on the page."""

    name = "known-answers"

    def __init__(self, gts: list[dict]):
        self.gts = gts

    def extract(self, layout, doc_type):
        gt = next(g for g in self.gts if g["fields"]["invoice_number"]["value"] in layout.text)
        return {k: ExtractedField(value=v["value"], confidence=0.99 if k != "bill_to" else 0.2)
                for k, v in gt["fields"].items()}


class ReportChat:
    """Summary/Q&A stand-in that answers only from the report's planted facts."""

    name = "known-answers"
    local = False

    def __init__(self, gt: dict):
        self.facts, self.calls = [f for f in gt["facts"] if f["sentence"]], 0

    def json(self, system, user):
        self.calls += 1
        if "Return JSON: {\"facts\"" in system:
            got = [f for f in self.facts if f["sentence"] in user]
            return {"facts": [{"fact": f["sentence"], "page": f["page"], "quote": f["sentence"]} for f in got]}
        if "executive summary" in system:
            return {"overview": [{"sentence": self.facts[0]["sentence"], "facts": [1]}], "key_points": [1, 2],
                    "explain": "A fictional company's annual report."}
        q = user.rsplit("Question:", 1)[-1].lower()
        f = next((f for f in self.facts if "dividend" in q and f["key"] == "dividend"), None)
        if not f:
            return {"answer": None, "citations": []}
        return {"answer": f"The board recommends ${f['answer']} per share.",
                "citations": [{"page": f["page"], "quote": f["sentence"]}]}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn
    tmp = tmp_path_factory.mktemp("ui")
    mp = pytest.MonkeyPatch()
    m = _app(tmp, mp, demo=False)
    mp.setattr(m, "LONGDOC_DIR", tmp / "ld")
    make_invoices(tmp / "inv", 3, 0.0, 5)
    gts = [json.loads(p.read_text()) for p in sorted((tmp / "inv" / "docs").glob("*.json"))]
    import docintel.jobs
    mp.setattr(docintel.jobs, "make_extractor", lambda spec: KnownAnswers(gts))
    rgt = make_reports(tmp / "rep", 1, 12, 4, scan=False)[0]
    import docintel.longdoc.index
    import docintel.longdoc.qa
    import docintel.longdoc.summarize
    mp.setattr(docintel.longdoc.summarize, "Chat", lambda *a: ReportChat(rgt))
    mp.setattr(docintel.longdoc.qa, "Chat", lambda *a: ReportChat(rgt))
    mp.setattr(docintel.longdoc.index, "embed", lambda *a, **k: None)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(m.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "inv": tmp / "inv" / "docs", "rep": tmp / "rep", "rgt": rgt, "gts": gts}
    server.should_exit = True
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
            try:
                b = p.chromium.launch(**kw)
                break
            except Exception:  # noqa: BLE001 - try the next browser
                b = None
        if b is None:
            pytest.skip("no browser available for Playwright")
        yield b
        b.close()


@pytest.fixture
def page(browser, site):
    ctx = browser.new_context(accept_downloads=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(site["url"] + "/app")
    yield pg
    ctx.close()
    assert not errors, errors  # no JavaScript errors in any flow


def test_batch_upload_download_and_journal(page, site, tmp_path):
    pdfs = sorted(str(p) for p in site["inv"].glob("*.pdf"))
    page.set_input_files("#fileInput", pdfs)
    page.wait_for_selector("#detail h1:has-text('Batch ready')", timeout=60000)
    assert page.locator("#detail table.docs tbody tr").count() == 3
    for name in ("invoices.csv", "line_items.csv", "journal_entries.csv", "quickbooks_journal.csv"):
        assert page.locator(f"#detail [data-dl$='{name}']").count() == 1, name
    with page.expect_download() as dl:
        page.click("#detail [data-dl='export.zip']")
    zpath = tmp_path / "out.zip"
    dl.value.save_as(zpath)
    with zipfile.ZipFile(zpath) as z:
        inv = z.read("invoices.csv").decode("utf-8-sig")
    for g in site["gts"]:
        assert g["fields"]["invoice_number"]["value"] in inv
    assert page.locator("#detail .entry").count() == 3  # one journal entry per invoice
    sel = page.locator("#detail select.acct").first
    sel.select_option("6030")
    page.wait_for_selector("#toast:has-text('Saved')", timeout=10000)


def test_review_fix_reaches_the_csv(page, site):
    page.click("#tabReview")
    page.wait_for_selector(".qitem", timeout=30000)
    page.locator(".qitem").first.click()
    bill_tos = [g["fields"]["bill_to"]["value"] for g in site["gts"]]
    page.wait_for_selector(".fields input", timeout=10000)
    field = next(el for el in page.locator(".fields input[id^='f']").all() if el.input_value() in bill_tos)
    field.fill("Reviewed Buyer Ltd")
    page.fill("#reviewer", "qa-bot")
    page.click("#submit")  # first press warns about unchecked fields ...
    page.wait_for_selector("#toast:has-text('Submit again')", timeout=10000)
    page.click("#submit")  # ... the second accepts them
    page.wait_for_selector("#toast:has-text('Saved')", timeout=10000)
    batches = page.evaluate("fetch('/batches').then(r => r.json())")
    csv = page.evaluate(f"fetch('/batches/{batches[0]['batch_id']}/export/invoices.csv').then(r => r.text())")
    assert "Reviewed Buyer Ltd" in csv and ",reviewed," in csv


def test_report_summary_citation_and_questions(page, site):
    page.click("#tabReports")
    page.set_input_files("#rfileInput", str(site["rep"] / "report00.pdf"))
    page.wait_for_selector("#rdetail h2:has-text('Summary')", timeout=60000)
    assert page.locator("#rdetail ol.claims li").count() >= 1
    page.locator("#rdetail .cite").first.click()
    for _ in range(150):  # polled from outside the page: the CSP forbids eval-based waits
        if page.evaluate("document.getElementById('pageImg').naturalWidth") > 0:
            break
        page.wait_for_timeout(100)
    assert page.evaluate("document.getElementById('pageImg').naturalWidth") > 0
    assert "Page" in page.inner_text("#pageQuote")
    page.click("#pageClose")
    page.fill("#askInput", "What final dividend per share does the board recommend?")
    page.press("#askInput", "Enter")
    page.wait_for_selector(".answer:not(.none) .cite", timeout=20000)
    page.fill("#askInput", "How many patents did the company file?")
    page.press("#askInput", "Enter")
    page.wait_for_selector(".answer.none:has-text('does not state')", timeout=20000)


def test_phone_layout_has_no_sideways_scroll(browser, site):
    ctx = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True)
    pg = ctx.new_page()
    pg.goto(site["url"] + "/app")
    for tab in ("#tabBatches", "#tabReports", "#tabReview"):
        pg.click(tab)
        pg.wait_for_timeout(800)
        assert pg.evaluate("document.documentElement.scrollWidth") <= 376, tab
    ctx.close()


def test_hostile_file_name_is_shown_as_text(page, site, tmp_path):
    evil = tmp_path / "<img src=x onerror=window.__xss=1>.pdf"
    try:
        evil.write_bytes(Path(next(site["inv"].glob("*.pdf"))).read_bytes())
    except OSError:
        pytest.skip("this file system does not allow < > in file names")
    page.set_input_files("#fileInput", str(evil))
    page.wait_for_selector("#detail table.docs tbody tr", timeout=60000)
    assert page.evaluate("window.__xss") is None
    assert "<img" in page.inner_text("#detail table.docs")


def test_landing_explains_and_leads_to_the_app(browser, site):
    for vp in ({"width": 1440, "height": 900}, {"width": 375, "height": 812}):
        ctx = browser.new_context(viewport=vp)
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e, errs=errors: errs.append(str(e)))
        pg.goto(site["url"] + "/")
        assert "Any invoice in" in pg.inner_text("h1")
        for section in ("#problem", "#what", "#proof", "#built", "#privacy"):
            assert pg.locator(section).count() == 1, section
        assert pg.evaluate("document.documentElement.scrollWidth") <= vp["width"] + 1
        pg.locator(".s-jnl [data-k='vat']").click()  # a copy's figure lights its source on the original
        assert pg.locator(".s-orig [data-k='vat'].lit").count() == 1
        pg.click(".hero a.btn.go")
        pg.wait_for_url("**/app")
        pg.wait_for_selector("#tabBatches")
        assert not errors, errors
        ctx.close()


def test_phone_review_switches_between_page_and_fields(browser, site):
    ctx = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True)
    pg = ctx.new_page()
    pg.goto(site["url"] + "/app")
    pg.click("#tabReview")
    pg.wait_for_selector(".qitem", timeout=30000)
    pg.wait_for_selector(".fields input", timeout=10000)
    assert pg.locator(".canvas").is_hidden() and pg.locator(".fields").is_visible()
    pg.click("#mvPage")
    assert pg.locator(".canvas").is_visible() and pg.locator(".fields").is_hidden()
    assert pg.locator(".keys").is_hidden()  # keyboard shortcuts are not shown on phones
    ctx.close()
