"""Every layout family renders, and the ground truth matches what is printed."""
import json

import pymupdf

from docintel.synth import layouts
from docintel.synth.intl_invoices import money


def test_every_family_prints_its_ground_truth(tmp_path):
    out = layouts.generate(tmp_path, 16, 0.0, 3, list(range(8)))
    assert set(out["by_layout"]) == set(layouts.NAMES)
    for gt_path in (tmp_path / "docs").glob("*.json"):
        gt = json.loads(gt_path.read_text())
        f = {k: v["value"] for k, v in gt["fields"].items()}
        text = " ".join(pymupdf.open(tmp_path / "docs" / gt["file"])[0].get_text().split())
        style = {"IN": "indian", "UK": "plain", "US": "plain"}.get(gt["locale"]) or layouts.EU[gt["locale"]][2]
        for name in ("invoice_number", "invoice_date", "vendor_name", "bill_to"):
            assert f[name] in text, (gt["doc_id"], name, f[name])
        assert money(f["total"], style) in text, gt["doc_id"]
        for row in f["line_items"]:
            assert row["description"] in text and money(row["amount"], style) in text, gt["doc_id"]


def test_held_out_families_are_not_trained_on():
    assert not set(layouts.HELD_OUT) & set(layouts.TRAIN_FAMILIES)
