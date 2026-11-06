# DocIntel: documents to spreadsheets, on your own computer

Drop in a folder of invoices, W-2s, pay stubs or bank statements (PDFs, scans or phone photos) and download CSV files: one row per document, with line items and bank transactions in their own files. A fine-tuned AI model reads each document, checks its own work, and sends only the fields it is unsure about to a person for review.

- **Private by default.** It runs on your computer with a local model through [Ollama](https://ollama.com). Documents never leave your machine, and there is no account or login.
- **Free and open source.** No API keys needed. Optionally use Google's Gemini free tier for extra accuracy.
- **Built for real documents.** It handles scans and photos (neural OCR), multi-page statements and line-item tables, and it checks that totals add up.

## Quick start

You need Python 3.11+ and [Ollama](https://ollama.com) (running).

```bash
git clone https://github.com/sivram04/doc-intelligence.git
cd doc-intelligence
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[local]"
python scripts/get_model.py                           # one time: downloads the model (~1.6 GB) into Ollama
docintel                                              # opens http://localhost:8030
```

Then, in the **Batches** tab, drop your files and click **All CSVs (.zip)**.

**Full guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) covers the CSV columns, reviewing fields, choosing a model, settings, the API and troubleshooting.

| You get | One row per |
|---|---|
| `invoices.csv`, `w2.csv`, `paystubs.csv`, `bank_statements.csv` | document |
| `line_items.csv`, `transactions.csv` | invoice line / bank transaction |
| `summary.csv` | uploaded file, with its status and any error |

**Accuracy on real receipts** (50 photographed receipts from the public CORD dataset, local model): total 89%, subtotal 84%, tax 65%. Details in the engineering notes below.

---

# Engineering notes

How the system was built and measured: synthetic benchmark, model race, fine-tuning, calibrated routing and the review loop. PRD: [`../03-document-intelligence.md`](../03-document-intelligence.md). Benchmarks use synthetic documents and public datasets only; no real personal documents.

## Status

| Phase | State |
|---|---|
| 0. Setup and data | Synthetic generator, case-grouped frozen splits (hashed test manifest), Docker Compose (API, Redis, Postgres, MLflow), DVC pipeline (remote not yet configured). |
| 1. Baseline pipeline | Preprocess (native vs scanned, deskew, denoise), PyMuPDF layout + key-value pairing, keyword classifier, rules extractor, LLM extractor via Project 1 gateway, async REST API. |
| 2. Validation | Field rules (sums, formats) and cross-document case checks with readable reasons. |
| 3. Model race | Learned doc-type classifier (TF-IDF + logistic regression, DVC stage). Extractors: rules, Qwen2.5-1.5B on CPU via Ollama, Gemini 3.5 Flash Lite (free tier); Groq / gateway adapters ready. Leaderboard with F1, latency, tokens and cost. |
| 4. Fine-tuning | QLoRA on Qwen2.5-1.5B (Kaggle T4, 34 min), GGUF q8_0 served by Ollama, MLflow (SQLite) tracking, champion/challenger gate. **Test F1 0.88 -> 0.97.** |
| 5. Routing + review | Calibrated field scores (confidence x OCR evidence x validation), thresholds fitted on val with a policy floor, plausibility rules, SQLite-backed review API, review console. **Auto-accept precision 96.8% -> 99.7%** at a 13% review rate. |
| 6–7 | Not started. |

## Developer setup

```bash
pip install -e ".[dev,ocr]"      # plus the Tesseract binary for scans
python scripts/make_data.py            # 60 cases (W-2 + pay stub + bank) + 60 invoices
python scripts/evaluate.py             # rules baseline on the frozen test split
python scripts/evaluate.py --extractor gemini:gemini-3.8-flash   # needs GEMINI_API_KEY in .env
uvicorn docintel.api.main:app --port 8080
pytest -q
```

### Data versioning (DVC)

`dvc.yaml` defines two stages: `make_data` (driven by `params.yaml`) and `evaluate`. `dvc.lock` pins the exact dataset hash, and `reports/eval_rules-v1_test.json` is tracked as a DVC metric.

```bash
python -m dvc repro          # regenerate data + eval only if inputs changed
python -m dvc metrics show
python -m dvc pull           # DagsHub remote; needs a DagsHub token in .dvc/config.local (see below)
python -m dvc push
```

Remote credentials (never committed):

```bash
python -m dvc remote modify origin --local access_key_id <dagshub-token>
python -m dvc remote modify origin --local secret_access_key <dagshub-token>
```

API: `POST /documents?case_id=...` (multipart file) → `GET /jobs/{id}` → `GET /cases/{case_id}/check`.

## Benchmark design

- **Held-out label wording.** Splits are assigned per case before rendering. Test documents use wording from `HELDOUT_LABELS` (e.g. "Take-home pay" instead of "Net pay"), and no extractor may import that dictionary. Train and val documents use `LABELS`.
- **Scans.** 40% of documents are rendered at 200 dpi, rotated up to 3°, blurred, given noise and paper tone, and saved as JPEG-degraded PNGs.
- **Fonts.** Helvetica, Times and Courier are mixed.
- **No leakage.** A case's W-2, pay stub and bank statement are always in the same split, and the test manifest is SHA-256 hashed.

## Baseline results (rules-v1, measured)

| Split | Label wording | Native F1 | Scan F1 | Doc-type accuracy |
|---|---|---|---|---|
| val (40 docs) | seen | 1.00 | 0.83 | 100% |
| **test (50 docs)** | **held out** | **0.23** | **0.22** | **74%** |

Test F1 by type: W-2 0.45, pay stub 0.19, invoice 0.11, bank statement 0.00 (the keyword classifier misses every held-out bank statement). Cross-document check: 0 of 3 seeded inconsistencies caught on test, because the extracted fields it needs are missing.

**What this says:** OCR on scans costs about 17 F1 points. Unseen label wording costs almost everything. That gap is what the model race (Phase 3: LLMs, LayoutLMv3) and fine-tuning (Phase 4) must close, and these numbers are the bar to beat.

Latency on a 16-thread laptop CPU: native PDF p50 is about 27 ms. A single scan takes about 1.8 s warm, mostly Tesseract. With 16 parallel workers, scan p95 rises to about 12 s from CPU contention. Overall throughput is about 96 docs/min.

## Model race (Phase 3, test split, held-out label wording)

![Field F1 by document type](reports/leaderboard.png)

| Model | Field F1 | Native | Scan | Doc-type acc. | Cross-doc caught | p50 latency | Tokens in/out | Cost / 1K docs |
|---|---|---|---|---|---|---|---|---|
| Gemini 3.5 Flash Lite (free tier, zero-shot) | **0.995** | 1.00 | 0.98 | 100% | 3 of 3 | 16 s native, 21 s scan* | 317 / 341 | n/a (free) |
| Qwen2.5-1.5B (Ollama, CPU, zero-shot) | 0.88 | 0.89 | 0.87 | 100% | 2 of 3 | 23 s native, 29 s scan | 312 / 144 | $0 (local) |
| rules-v1 + learned classifier | 0.38 | 0.42 | 0.37 | 100% | 0 of 3 | <0.1 s native, ~2 s scan | – | $0 |

- **Classifier.** The learned classifier fixed document typing (74% to 100% on test) and lifted rules-v1 bank-statement F1 from 0.00 to 0.67.
- **Small model.** A 1.5B model with no training already beats rules by 50 F1 points on unseen wording. The cost is latency: about 25 s per document on this laptop CPU, with 2 parallel requests.
- **Review rate.** It is 78% for Qwen, because the model's self-reported confidence is uncalibrated and sits below the 0.85 auto-accept bar. Calibration and per-field thresholds are Phase 5.
- **Gemini 3.5 Flash Lite** is close to perfect: 0.995 F1, every planted cross-document mismatch caught, and only a 2% review rate because its confidence scores are high. \*Its latency includes waiting for client-side pacing at 12 requests/min, so it overstates model time.
- **The gap to close in Phase 4:** a hosted model scores 0.995, while the free local 1.5B model scores 0.88. Fine-tuning the small model aims to close most of that gap at $0 per document and with no data leaving the machine.
- **Free-tier limits** (checked 2026-10-28): Gemini 3.8 Flash allows 20 requests/day, too few for a 50-document run, so the race uses Flash Lite (500/day).
- **Not on the board yet:** Gemma 4 31B, because the API returned 503 "high demand" on every request during the run. Groq, because no key is configured. Rerun `bash scripts/race.sh` once either is available.
- **Cost.** API costs show "n/a" until list prices are filled in `docintel/eval/prices.py` for the run date.
- **Not used:** PyTorch CPU inference. Through `transformers` it ran about 30x slower than llama.cpp on this machine, so `hf:` is reserved for the Kaggle/Colab GPU in Phase 4.

Full table: [`reports/leaderboard.md`](reports/leaderboard.md).

## Fine-tuning (Phase 4)

| | Zero-shot Qwen2.5-1.5B | **Fine-tuned (QLoRA)** | Gemini 3.5 Flash Lite |
|---|---|---|---|
| Test field F1 (unseen wording) | 0.878 | **0.968** | 0.995 |
| Invoice / Bank / Pay stub / W-2 | 0.84 / 0.82 / 0.87 / 0.96 | 0.90 / 0.99 / 0.98 / 0.98 | 1.00 / 1.00 / 1.00 / 0.98 |
| Scan F1 | 0.87 | 0.95 | 0.98 |
| Review rate (conf. < 0.85) | 78% | **11%** | 2% |
| Cross-doc mismatches caught | 2 of 3 | **3 of 3** | 3 of 3 |
| Cost per 1K docs | $0 | $0 | free tier only |
| Data leaves the machine | no | no | yes |

**Results**

- Fine-tuning closed 80% of the gap between the free local model and the hosted API, and cut the review rate from 78% to 11%.
- The training targets encode confidence: 1.0 when the value is legible in the OCR text, 0.5 when it is not. The tuned model's scores now separate safe fields from unsafe ones.

**Training setup**

- **Data:** 1,360 chat examples, from the train split plus 900 augmentation documents rendered with *train* wording only. Test wording was never seen in training.
- **Recipe:** QLoRA (4-bit NF4 base, LoRA r=16 on all projections), 2 epochs, loss on the answer only. Train loss 0.0073, val loss 0.0037. Seed 42; reruns reproduce the same losses.
- **Serving:** merged and converted to GGUF q8_0 on Kaggle, then `ollama create` from [`training/Modelfile`](training/Modelfile).
- **Champion gate:** a challenger is promoted only if its test F1 is at least 0.005 above the champion's. The decision and both runs are logged to MLflow; the current champion is in `reports/champion.json`.

**Trade-off:** the q8_0 GGUF (1.6 GB) is slower on this CPU than the base model's q4 build, at about 40 s per document vs 25 s. A q4_K_M export is a planned speed-up.

**Reproduce**

```bash
python -m dvc repro build_sft                 # SFT data (DVC-tracked)
bash scripts/kaggle_train.sh                  # upload + train on Kaggle T4 (needs KAGGLE_API_TOKEN)
bash scripts/kaggle_train.sh status
bash scripts/kaggle_train.sh fetch            # -> models/finetuned/{model-q8_0.gguf, metrics.json}
cd training && ollama create docintel-qwen1.5b-ft -f Modelfile && cd ..
python scripts/evaluate.py --extractor ollama:docintel-qwen1.5b-ft --workers 2
python scripts/register_model.py --name docintel-qwen1.5b-ft-v1 --extractor ollama:docintel-qwen1.5b-ft     --train-metrics models/finetuned/metrics.json
```

## Confidence routing and review (Phase 5)

The fine-tuned model reports high confidence on almost everything, so its raw score cannot tell right from wrong. Each field gets a calibrated score instead:

- **Model confidence**
- **x OCR evidence:** the value must literally appear in the span it cites. OCR noise characters (`~ | _`) don't count as evidence.
- **x Validation:** fields from a document that fails a rule are penalised decisively. The rules include sums and plausibility checks (tax above subtotal suggests swapped fields; deductions above gross pay).

Thresholds are fitted on val for a target precision of 99%, per document type where val has at least 40 fields of that type. A **policy floor of 0.85** stops the fit from ever accepting fields from flagged documents; on only 30 val documents, the fit alone learned to do exactly that.

| Test split, fine-tuned Qwen (402 fields) | Raw confidence >= 0.85 | **Calibrated routing** |
|---|---|---|
| Fields sent to review | 0% | 13.4% |
| Precision of auto-accepted fields | 96.8% | **99.7%** |
| Wrong values that skip review | 3.2% (13 fields) | **0.25% (1 field)** |

Error analysis that drove the rules:

- 8 of the 13 errors were already flagged by validators but were scored too gently.
- 2 were subtotal and tax swapped on a scan, which balances the sum, so the new plausibility rule catches them.
- 2 were OCR noise.

Reproduce with `python scripts/calibrate.py --target 0.99` (reads `reports/preds_*` from `evaluate.py`; writes `models/thresholds.json` and `reports/routing_*.json`).

### Review console

`EXTRACTOR=ollama:docintel-qwen1.5b-ft uvicorn docintel.api.main:app --port 8030`, then open http://localhost:8030 and click **Load 6 demo documents** (synthetic only).

- **Queue:** documents ordered by number of fields to check, with flagged documents marked.
- **Page view:** shows the image OCR actually read; scans are served deskewed, so the boxes line up. Boxes are amber when a field needs a check and green when it was auto-accepted; the active field is ringed.
- **Fields:** editable values with calibrated status, confidence and the OCR source text.
- **Keyboard:** `Up`/`Down` move between fields, `Enter` confirms, `E` edits, `Esc` stops editing, `Ctrl+Enter` submits (warns once if fields are unchecked), `N` goes to the next document.
- **Persistence:** reviews and corrections are stored in SQLite. Every correction is also appended to `data/corrections/corrections.jsonl`, the input to the Phase 6 retraining flywheel. Header stats show the queue, documents reviewed, corrections and seconds per document.

API: `GET /review/queue`, `GET /jobs/{id}/page/{n}.png`, `POST /jobs/{id}/review`, `GET /review/stats`, `POST /demo/seed`.

## Layout

```
docintel/
  schemas.py          Pydantic schema per doc type; ExtractedField = value + confidence + bbox
  synth/              generator.py (ReportLab + Faker, ground truth JSON), split.py (frozen splits)
  pipeline/           preprocess.py, ocr.py, classify.py, run.py (process + confidence routing)
  extract/            rules.py, llm.py (OpenAI-compatible + HF local), registry.py (spec -> extractor)
  validators.py       field + cross-document rules
  eval/metrics.py     field-level P/R/F1
  api/main.py         FastAPI async jobs
scripts/              make_data.py, train_classifier.py, evaluate.py, leaderboard.py, race.sh,
                      build_sft.py, kaggle_train.sh, register_model.py
training/             train_qlora.py (Kaggle job), Modelfile (Ollama)
docintel/routing.py   calibrated field scores + threshold fitting
docintel/store.py     SQLite jobs / reviews / corrections
docintel/api/static/  review console (single HTML file, no build step)
```
