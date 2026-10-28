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
        assert all(t in (str, float, int) for t in field_types(dt).values())


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


def test_deskew_recovers_angle():
    img = np.full((400, 400), 255, np.uint8)
    img[150:250, 50:350] = 0
    import cv2
    m = cv2.getRotationMatrix2D((200, 200), 5, 1.0)
    rot = cv2.warpAffine(img, m, (400, 400), borderValue=255)
    _, angle = deskew(rot)
    assert abs(abs(angle) - 5) < 1.0
