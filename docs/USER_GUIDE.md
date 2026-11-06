# DocIntel user guide

DocIntel turns a folder of financial documents into spreadsheets. Drop in invoices, W-2s, pay stubs or bank statements (PDFs, scans or phone photos) and download CSV files: one row per document, with line items and bank transactions in their own files.

It runs on your own computer. Your documents, the extracted data and the CSVs never leave your machine, and there is no account or login.

- [1. What you need](#1-what-you-need)
- [2. Install](#2-install)
- [3. Start the app](#3-start-the-app)
- [4. Turn documents into a CSV](#4-turn-documents-into-a-csv)
- [5. Check the fields it is unsure about](#5-check-the-fields-it-is-unsure-about)
- [6. What is in each CSV](#6-what-is-in-each-csv)
- [7. Choosing a model](#7-choosing-a-model)
- [8. Settings](#8-settings)
- [9. Use it from a script (API)](#9-use-it-from-a-script-api)
- [10. Troubleshooting](#10-troubleshooting)
- [11. Privacy](#11-privacy)

## 1. What you need

| | Minimum | Notes |
|---|---|---|
| Computer | Windows 10/11, macOS 12+ or Linux, 8 GB RAM | 16 GB is more comfortable. No GPU needed. |
| Disk | 4 GB free | About 1.6 GB of it is the model. |
| Python | 3.11 or newer | [python.org/downloads](https://www.python.org/downloads/) |
| Ollama | Any recent version | Runs the AI model locally. [ollama.com](https://ollama.com) |
| Git | Any | Or download the repository as a zip from GitHub. |

**Optional:** [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) improves table reading on clean scans. Without it, scans and photos are still read by the built-in neural OCR.

## 2. Install

Run these once, in a terminal:

```bash
git clone https://github.com/sivram04/document-intelligence.git
cd document-intelligence
python -m venv .venv
```

Activate the environment:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate
```

Install the app, then download the model into Ollama (one time, about 1.6 GB):

```bash
pip install -e ".[local]"
python scripts/get_model.py
```

Make sure Ollama is running first: open the Ollama app, or run `ollama serve` in another terminal.

## 3. Start the app

```bash
docintel
```

Your browser opens at **http://localhost:8030**. Leave the terminal open while you work, and press `Ctrl+C` in it to stop the app.

The app listens only on your own computer (`127.0.0.1`), so nobody else on your network can reach it.

## 4. Turn documents into a CSV

1. Open the **Batches** tab.
2. Drag your files onto **Drop PDFs or images here**, or click it to choose files. You can select many at once, up to 200 per batch.
   - Accepted: PDF, PNG, JPEG and TIFF, up to 25 MB each. A PDF can have several pages.
   - Anything else is listed under **Not processed** with the reason; the rest of the batch still runs.
3. Watch the progress bar. Each document shows one of:
   - **Auto-approved:** every field passed the checks.
   - **N to check:** some fields are uncertain (see section 5).
   - **Flagged:** a check failed, for example the line items do not add up to the total.
   - **Failed:** the file could not be read; the reason is in the **Notes** column.
4. Click **All CSVs (.zip)**, or a single file such as **invoices.csv**.

You can download at any time, even before the batch finishes: the CSVs contain whatever is done so far, and `summary.csv` lists the rest.

**How long it takes:** with the local model on a typical laptop CPU, about 20–60 seconds per document. 50 invoices take roughly 20–45 minutes. You can keep using the computer, or leave it running.

## 5. Check the fields it is unsure about

The app scores every field it extracts. It accepts a field automatically only when the model is confident, the value can be found in the page text, and the document passes its checks. Everything else is sent to review, so you look only at the fields that need it.

1. Click **Review N documents** on the batch, or open the **Review** tab.
2. The page is shown with the extracted values highlighted: **amber** fields need a check, **green** ones were accepted automatically.
3. Fix a value by typing in its box. Tables (line items, transactions) can be edited cell by cell, and you can add or remove rows.
4. Click **Submit review**. The next document opens.

Keyboard: `↑`/`↓` move between fields, `Enter` confirms a field, `E` edits it, `Ctrl+Enter` submits, `N` skips to the next document.

The CSV always uses your corrected values. Its `review_status` column says which rows were checked by a person (`reviewed`) and which were not (`auto_approved` or `needs_review`).

Your corrections are also saved to `data/corrections/corrections.jsonl`, which can be used to retrain the model on your own documents.

## 6. What is in each CSV

The download contains only the files that apply to your batch.

| File | One row per | Columns |
|---|---|---|
| `invoices.csv` | invoice | file, review_status, vendor_name, invoice_number, invoice_date, due_date, bill_to, subtotal, discount, tax, shipping, total, flags |
| `line_items.csv` | invoice line | file, invoice_number, row, description, quantity, unit_price, amount |
| `w2.csv` | W-2 | file, review_status, employer and employee details, tax_year, the wage and tax boxes, state fields, flags |
| `paystubs.csv` | pay stub | file, review_status, employer_name, employee_name, pay_date, pay_frequency, gross_pay, total_deductions, net_pay, ytd_gross, flags |
| `bank_statements.csv` | statement | file, review_status, bank_name, account_holder, account_last4, statement_period, balances and totals, flags |
| `transactions.csv` | bank transaction | file, account_last4, row, date, description, amount |
| `summary.csv` | uploaded file | file, doc_type, status, review_status, fields_to_review, flags, error |

- **Joining tables:** `line_items.csv` and `transactions.csv` link back to their document by `file`.
- **Amounts** are plain numbers (`1284.5`, not `$1,284.50`), so spreadsheets can sum them. Negative amounts, such as discounts and withdrawals, are negative numbers.
- **Empty cells** mean the field was not found on the document.
- **Dates** are kept as printed on the document.
- **Opening in Excel:** the files are UTF-8, so names with accents display correctly.
- **Safety:** text that a spreadsheet would run as a formula (starting with `=`, `+`, `-` or `@`) is prefixed with `'`.

## 7. Choosing a model

Set `EXTRACTOR` in a file named `.env` in the project folder (copy `.env.example`), then restart `docintel`.

| `EXTRACTOR` | Cost | Your documents leave your computer? | Notes |
|---|---|---|---|
| `ollama:docintel-qwen1.5b-ft-v3` (default) | Free | No | Fine-tuned for these documents. About 20–60 s per document on a CPU. |
| `gemini:gemini-3.5-flash-lite` | Free tier | Yes, to Google | Faster and a little more accurate on hard scans. Needs `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey). The free tier allows about 500 documents a day. |
| `cascade:ollama:docintel-qwen1.5b-ft-v3\|gemini:gemini-3.5-flash-lite` | Mostly free | Only the documents the local model is unsure about | Local model first; a document goes to Gemini only when the local answer fails its checks. |
| `rules` | Free | No | No AI; very fast but weak. For testing only. |

Measured accuracy on 50 real photographed receipts ([CORD dataset](https://github.com/clovaai/cord)):

| Field | Local model (`docintel-qwen1.5b-ft-v3`) | Gemini 3.5 Flash Lite |
|---|---|---|
| Total | 89% | 85% |
| Subtotal | 84% | 90% |
| Tax | 65% | 75% |

These are small samples (47, 31 and 20 receipts), so treat close numbers as ties. Accuracy on your own documents depends on their layout and scan quality, which is why the review step exists.

## 8. Settings

Put any of these in `.env`:

| Setting | Default | What it does |
|---|---|---|
| `EXTRACTOR` | `ollama:docintel-qwen1.5b-ft-v3` | Which model reads the documents (section 7). |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama runs. |
| `WORKERS` | `2` | Documents processed at the same time. On a CPU, more than 2 rarely helps. |
| `MAX_UPLOAD_MB` | `25` | Largest file accepted. |
| `MAX_BATCH_FILES` | `200` | Most files in one batch. |
| `DOCINTEL_DB` | `data/docintel.db` | Where results are stored (SQLite). |
| `UPLOAD_DIR` | `data/uploads` | Where uploaded files are kept. |
| `API_KEYS` | empty | Comma-separated keys. Leave empty on your own computer; set it if you expose the app to a network. |

## 9. Use it from a script (API)

The console uses a plain HTTP API, so you can automate it. For example, to process a folder:

```bash
# Upload every PDF in ./invoices as one batch
curl -s $(for f in invoices/*.pdf; do printf -- '-F files=@%s ' "$f"; done) http://localhost:8030/batches
# -> {"batch_id": "6c01873b0c7f", "accepted": [...], "refused": [...]}

curl -s http://localhost:8030/batches/6c01873b0c7f            # progress
curl -s -o out.zip http://localhost:8030/batches/6c01873b0c7f/export.zip
curl -s http://localhost:8030/batches/6c01873b0c7f/export/invoices.csv
```

| Endpoint | Purpose |
|---|---|
| `POST /batches` | Upload many files (`files` form field, repeated). |
| `GET /batches` | Your batches. |
| `GET /batches/{id}` | Progress and per-document status. |
| `GET /batches/{id}/export.zip` | All CSVs. |
| `GET /batches/{id}/export/{name}.csv` | One CSV, e.g. `invoices`, `line_items`, `summary`. |
| `POST /documents` | Upload a single file. |
| `GET /jobs/{id}` | Full result for one document: values, confidence, page positions. |
| `GET /review/queue`, `POST /jobs/{id}/review` | The review loop. |

Interactive API docs: http://localhost:8030/docs

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| `Ollama is not running` | Open the Ollama app, or run `ollama serve`, then start `docintel` again. |
| `Model ... is not in Ollama yet` | Run `python scripts/get_model.py`. |
| `docintel: command not found` | Activate the environment first (section 2), or run `python -m docintel`. |
| Port 8030 is in use | `docintel --port 8040` |
| A document is very slow | Normal for long scans on a CPU. Long multi-page bank statements can take a few minutes. |
| A document is the wrong type, or fields are empty | Check its page in the Review tab. Very blurry photos and handwritten documents are not supported. |
| Status stays **Queued** after a restart | Documents queued when the app stopped are not resumed; upload them again. |
| Start again from empty | Stop the app and delete the `data/` folder. |

## 11. Privacy

- **Local by default:** with the default model, everything happens on your computer. Uploads are stored in `data/uploads`, and results in `data/docintel.db`.
- **When something leaves your computer:** only if you choose a hosted model (`gemini:` or `cascade:`). Then the page text of each document sent to it goes to that provider, under their terms.
- **Deleting your data:** delete the `data/` folder.

### The public demo

The online demo is for trying the app with a document or two, not for real work. Each visitor sees only their own documents, and every upload is deleted after 24 hours. It processes 2 documents per visitor per day, of up to 3 pages each, using a hosted model. Please don't upload documents with real personal data to it.

To process more, install it on your own computer as above. It is free and open source.

## Run the full stack with Docker (optional)

For a shared server, there is also a production setup: API, a separate worker, Redis queue, Postgres and Prometheus metrics.

```bash
docker compose up -d --build api worker redis postgres prometheus
```

The console is then at http://localhost:8030. Set `API_KEYS` in `.env` before exposing it to a network.
