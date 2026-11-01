import random

import numpy as np
import pytest
from faker import Faker

from docintel.eval.metrics import FieldScorer
from docintel.extract.base import coerce
from docintel.pipeline.preprocess import deskew
from docintel.pipeline.run import process, route
from docintel.schemas import SCHEMAS, DocType, field_types
from docintel.synth.generator import make_case, make_invoice, render
from docintel.validators import validate_case, validate_document


@pytest.fixture(scope="module")
def case_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("docs")
    rng, fk = random.Random(1), Faker()
    fk.seed_instance(1)
    docs = make_case("caseX", fk, rng, None) + [make_invoice("invX", fk, rng)]
    for doc in docs:
        render(doc, d, rng)
    return d, docs


def test_schemas_have_value_types():
    for dt in SCHEMAS:
        assert all(t in (str, float, int, list) for t in field_types(dt).values())


def test_coerce():
    assert coerce("$12,345.67", float) == 12345.67
    assert coerce("2024", int) == 2024
    assert coerce("n/a", float) is None


def test_pipeline_extracts_synthetic_docs(case_dir):
    d, docs = case_dir
    for doc in docs:
        res = process(d / f"{doc.doc_id}.pdf")
        assert res.doc_type.value == doc.doc_type
        s = FieldScorer()
        s.add(doc.doc_type, {k: f.value for k, f in res.fields.items()}, doc.values)
        assert s.report()[doc.doc_type]["f1"] >= 0.9
        assert res.flags == []
        assert set(route(res)["auto"]) | set(route(res)["review"]) == set(res.fields)


def test_field_validators():
    assert validate_document(DocType.INVOICE, {"subtotal": {"value": 100.0}, "tax": {"value": 8.0}, "total": {"value": 110.0}})
    assert not validate_document(DocType.INVOICE, {"subtotal": {"value": 100.0}, "tax": {"value": 8.0}, "total": {"value": 108.0}})
    assert validate_document(DocType.W2, {"employer_ein": {"value": "123456789"}})


@pytest.mark.parametrize("kind", ["w2_inflated", "deposits_low"])
def test_cross_doc_catches_seeded_inconsistency(kind):
    rng, fk = random.Random(3), Faker()
    for bad, expect in ((None, False), (kind, True)):
        docs = make_case("c", fk, rng, bad)
        flags = validate_case({DocType(x.doc_type): {k: {"value": v} for k, v in x.values.items()} for x in docs})
        assert bool(flags) is expect, flags


def test_split_is_stable_and_case_grouped():
    from docintel.synth.split import assign_split
    assert assign_split("case0001", 42) == assign_split("case0001", 42)
    splits = {assign_split(f"case{i:04d}", 42) for i in range(200)}
    assert splits == {"train", "val", "test"}


def test_heldout_labels_never_overlap_train_labels():
    from docintel.synth.generator import HELDOUT_LABELS, LABELS
    for dt, fields in HELDOUT_LABELS.items():
        assert set(fields) == set(LABELS[dt])
        for name, label in fields.items():
            assert label.lower() not in {v.lower() for v in LABELS[dt][name]}


def test_scan_variant_writes_png(tmp_path):
    rng, fk = random.Random(5), Faker()
    doc = make_invoice("invS", fk, rng)
    render(doc, tmp_path, rng, scan=True)
    assert (tmp_path / "invS.png").exists() and not (tmp_path / "invS.pdf").exists()


def test_llm_parser_tolerates_fences_and_bad_items(case_dir):
    from docintel.extract.llm import LLMExtractor, parse_json
    from docintel.pipeline.ocr import analyze
    from docintel.pipeline.preprocess import load_pages

    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("no json here") == {}

    class Fake(LLMExtractor):
        def _chat(self, system, user):
            return '{"subtotal": {"value": "$1,200.50", "confidence": 0.9, "span": 0}, "tax": "8", "total": null}'

    d, docs = case_dir
    inv = next(x for x in docs if x.doc_type == "invoice")
    layout = analyze(*load_pages(str(d / f"{inv.doc_id}.pdf")))
    out = Fake().extract(layout, DocType.INVOICE)
    assert out["subtotal"].value == 1200.5 and out["subtotal"].bbox is not None
    assert out["tax"].value == 8.0 and out["total"].value is None and out["total"].confidence == 0.0


def test_registry_rejects_unknown_and_missing_keys(monkeypatch):
    from docintel.extract.registry import make_extractor
    with pytest.raises(ValueError):
        make_extractor("nope:x")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        make_extractor("groq:some-model-unset")


def test_deskew_recovers_angle():
    img = np.full((400, 400), 255, np.uint8)
    img[150:250, 50:350] = 0
    import cv2
    m = cv2.getRotationMatrix2D((200, 200), 5, 1.0)
    rot = cv2.warpAffine(img, m, (400, 400), borderValue=255)
    _, angle = deskew(rot)
    assert abs(abs(angle) - 5) < 1.0


def test_evidence_and_threshold_fit():
    from docintel.routing import field_score, fit_threshold, has_evidence
    assert has_evidence(3083.3, "$3,083.30") and has_evidence(2024, "Tax year: 2024")
    assert has_evidence("Taylor Inc", "Company: Taylor Inc.") and not has_evidence(3083.3, "$3,088.30")
    assert field_score({"value": 1.0, "confidence": 1.0, "source_text": "$1.00"}, False) == 1.0
    assert field_score({"value": 1.0, "confidence": 1.0, "source_text": "$7.00"}, True) == pytest.approx(0.30)
    pts = [(1.0, True)] * 99 + [(1.0, False)] + [(0.6, False)] * 5 + [(0.6, True)] * 5
    assert fit_threshold(pts, 0.99) == 1.0
    assert fit_threshold(pts, 0.999) is None


def test_review_api_roundtrip(tmp_path, monkeypatch, case_dir):
    import importlib
    import time as _time

    from fastapi.testclient import TestClient

    monkeypatch.setenv("DOCINTEL_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("CORRECTIONS_LOG", str(tmp_path / "corr.jsonl"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("THRESHOLDS", str(tmp_path / "none.json"))  # default 0.85 cut-off
    import docintel.routing
    import docintel.store
    importlib.reload(docintel.store)
    importlib.reload(docintel.routing)
    import docintel.api.main as m
    m = importlib.reload(m)
    c = TestClient(m.app)

    d, docs = case_dir
    inv = next(x for x in docs if x.doc_type == "invoice")
    jid = c.post("/documents", files={"file": ("inv.pdf", (d / f"{inv.doc_id}.pdf").read_bytes())}).json()["job_id"]
    for _ in range(100):
        if c.get(f"/jobs/{jid}").json()["status"] in ("done", "failed"):
            break
        _time.sleep(0.1)
    job = c.get(f"/jobs/{jid}").json()
    assert job["status"] == "done" and c.get(f"/jobs/{jid}/page/0.png").headers["content-type"] == "image/png"
    fields = {k: f["value"] for k, f in job["result"]["fields"].items()}
    fields["total"] = 1.23
    r = c.post(f"/jobs/{jid}/review", json={"fields": fields, "reviewer": "t", "seconds": 4.2}).json()
    assert r["fields_corrected"] == 1
    assert c.post(f"/jobs/{jid}/review", json={"fields": {"nope": 1}, "reviewer": "t", "seconds": 1}).status_code == 422
    s = c.get("/review/stats").json()
    assert s["documents_reviewed"] == 1 and s["fields_corrected"] == 1
    assert '"corrected": 1.23' in (tmp_path / "corr.jsonl").read_text()


def test_plausibility_rules_and_noise():
    from docintel.routing import has_evidence
    assert any("swapped" in f for f in validate_document(
        DocType.INVOICE, {"subtotal": {"value": 326.33}, "tax": {"value": 3955.49}, "total": {"value": 4281.82}}))
    assert not has_evidence("Sanders~Espinoza", "Sanders~Espinoza")


def test_upload_security_and_pii_mask(tmp_path, monkeypatch):
    import importlib

    from fastapi.testclient import TestClient

    from docintel.obs import mask
    assert "123-45-6789" not in mask("ssn 123-45-6789 acct 1234567890123")
    monkeypatch.setenv("DOCINTEL_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("API_KEYS", "secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    import docintel.store
    importlib.reload(docintel.store)
    import docintel.api.main as m
    m = importlib.reload(m)
    c = TestClient(m.app)
    assert c.get("/review/stats").status_code == 401
    h = {"X-API-Key": "secret"}
    assert c.get("/review/stats", headers=h).status_code == 200
    r = c.post("/documents", headers=h, files={"file": ("fake.pdf", b"MZ not a pdf")})
    assert r.status_code == 415
    assert c.get("/ready").json()["ok"] is True
    assert b"docintel_jobs_total" in c.get("/metrics").content


def test_coerce_handles_wrapped_cells_and_junk_numbers():
    cols = {"description": str, "amount": float}
    assert coerce([{"description": {"value": "Laptop", "confidence": 1}, "amount": {"value": "$1,200.50"}}], list, cols)         == [{"description": "Laptop", "amount": 1200.5}]
    assert coerce(",", float) is None and coerce("-", float) is None and coerce("(45.10)", float) == -45.1


def test_dot_grouped_thousands():
    assert coerce("60.000", float) == 60000.0 and coerce("Rp 1.250.000", float) == 1250000.0
    assert coerce("60.50", float) == 60.5
