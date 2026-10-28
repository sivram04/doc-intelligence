# Document Intelligence Engine

Reads financial documents (W-2s, pay stubs, bank statements, invoices), extracts validated structured data with per-field confidence and source boxes, and cross-checks documents within a case. PRD: [`../03-document-intelligence.md`](../03-document-intelligence.md). Synthetic data only; no real personal documents.

## Status

| Phase | State |
|---|---|
| 0. Setup and data | Synthetic generator, case-grouped frozen splits (hashed test manifest), Docker Compose (API, Redis, Postgres, MLflow), DVC pipeline (remote not yet configured). |
| 1. Baseline pipeline | Preprocess (native vs scanned, deskew, denoise), PyMuPDF layout + key-value pairing, keyword classifier, rules extractor, LLM extractor via Project 1 gateway, async REST API. |
| 2. Validation | Field rules (sums, formats) and cross-document case checks with readable reasons. |
| 3–7 | Not started. |

## Quickstart

```bash
pip install -e ".[dev]"
python scripts/make_data.py            # 60 cases (W-2 + pay stub + bank) + 60 invoices
python scripts/evaluate.py             # rules baseline on the frozen test split
python scripts/evaluate.py --model gemini-2.5-flash   # needs the Project 1 gateway (.env)
uvicorn docintel.api.main:app --port 8080
pytest -q
```

### Data versioning (DVC)

`dvc.yaml` defines two stages: `make_data` (driven by `params.yaml`) and `evaluate`. `dvc.lock` pins the exact dataset hash, and `reports/eval_rules-v1_test.json` is tracked as a DVC metric.

```bash
python -m dvc repro          # regenerate data + eval only if inputs changed
python -m dvc metrics show
python -m dvc remote add -d storage gdrive://<folder-id>   # or an HF Datasets / S3-compatible remote
python -m dvc push
```

API: `POST /documents?case_id=...` (multipart file) → `GET /jobs/{id}` → `GET /cases/{case_id}/check`.

## First measured numbers (rules-v1, test split, 62 docs)

Field F1 is 1.00 on every type, classification is 100%, the cross-document check caught 6 of 6 seeded inconsistencies with 0 false positives, and throughput was about 3,900 docs/min on a laptop CPU.

**Treat these as a sanity check, not a result.** The current synthetic set is too easy. The PDFs are clean and have native text, and the rules extractor uses the generator's own label dictionary. The next data step is making the benchmark hard:
- Rasterised "scan" variants with skew, blur and noise (needs Tesseract or PaddleOCR).
- Label wording, fonts and table layouts that are held out from the extractor.
- Public sets (FUNSD, CORD, SROIE).

## Layout

```
docintel/
  schemas.py          Pydantic schema per doc type; ExtractedField = value + confidence + bbox
  synth/              generator.py (ReportLab + Faker, ground truth JSON), split.py (frozen splits)
  pipeline/           preprocess.py, ocr.py, classify.py, run.py (process + confidence routing)
  extract/            rules.py (baseline), llm.py (gateway: Gemini / Groq / Ollama fine-tune)
  validators.py       field + cross-document rules
  eval/metrics.py     field-level P/R/F1
  api/main.py         FastAPI async jobs
scripts/              make_data.py, evaluate.py
```
