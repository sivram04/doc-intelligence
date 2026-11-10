"""Deploy the public demo to a Hugging Face Space (Docker SDK).

Builds a small folder (app code, a Space Dockerfile, the Space README and a handful of synthetic sample
invoices), creates the Space if it does not exist, stores GEMINI_API_KEY as a Space *secret*, sets the demo
settings as Space variables, and uploads. Reads HF_TOKEN and GEMINI_API_KEY from the environment (.env).

    python scripts/deploy_hf_space.py --space SivaramAI/docintel            # private (default)
    python scripts/deploy_hf_space.py --space SivaramAI/docintel --public   # only when you are ready

Commits are authored as the Space owner with Hugging Face's no-reply address, never a personal email.
"""
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ["intl0002-uk", "intl0000-in", "intl0003-us", "intl0004-de", "intl0005-fr", "intl0001-in"]  # VAT, GST, sales tax

DOCKERFILE = """FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0 curl \\
    && rm -rf /var/lib/apt/lists/*
# Hugging Face runs Spaces as uid 1000
RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user pyproject.toml .
COPY --chown=user docintel docintel
RUN pip install --no-cache-dir ".[ocr]"
COPY --chown=user samples samples
RUN mkdir -p /app/data/uploads /app/data/corrections /app/data/longdocs && chown -R user /app/data
USER user
ENV PYTHONUNBUFFERED=1 DEMO_DIR=/app/samples DOCINTEL_DB=/app/data/docintel.db UPLOAD_DIR=/app/data/uploads \\
    LONGDOC_DIR=/app/data/longdocs CORRECTIONS_LOG=/app/data/corrections/corrections.jsonl
EXPOSE 7860
CMD ["uvicorn", "docintel.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
"""

README = """---
title: DocIntel
emoji: 🧾
colorFrom: yellow
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Invoices to CSV and balanced journal entries, every number cited
---

# DocIntel public demo

Drop in an invoice and get a CSV row and a balanced double-entry journal entry, with every value traced to its
place on the page. Long reports get a cited summary you can question.

This demo reads documents with Google's Gemini API, takes 2 documents per visitor per day, and deletes files
after 24 hours. Please upload only sample or public documents. The open-source version runs entirely on your own
computer with a fine-tuned local model.
"""

VARIABLES = {
    "DEMO_MODE": "1", "DEMO_MAX_DOCS": "2", "DEMO_MAX_PAGES": "3", "DEMO_DAILY_CAP": "150", "DEMO_TTL_HOURS": "24",
    "DEMO_LONGDOC_PAGES": "15", "DEMO_MAX_QUESTIONS": "10", "TRUST_PROXY_HOPS": "1",
    "EXTRACTOR": "gemini:gemini-3.5-flash-lite", "SUMMARY_MODEL": "gemini:gemini-3.5-flash-lite", "WORKERS": "2",
}


def build(dst: Path) -> None:
    shutil.copy(ROOT / "pyproject.toml", dst)
    shutil.copytree(ROOT / "docintel", dst / "docintel", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dst / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (dst / "README.md").write_text(README, encoding="utf-8")
    docs = dst / "samples" / "docs"
    docs.mkdir(parents=True)
    src = ROOT / "data" / "synthetic_intl" / "docs"
    for d in SAMPLES:
        for ext in (".json", ".pdf"):
            shutil.copy(src / f"{d}{ext}", docs)
    (dst / "samples" / "splits.json").write_text(json.dumps({"splits": {"test": SAMPLES}}), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="owner/name, e.g. SivaramAI/docintel")
    ap.add_argument("--public", action="store_true", help="make the Space public (default: private)")
    a = ap.parse_args()
    token, gemini = os.environ.get("HF_TOKEN"), os.environ.get("GEMINI_API_KEY")
    if not token or not gemini:
        raise SystemExit("HF_TOKEN and GEMINI_API_KEY must be set (put them in .env)")
    api = HfApi(token=token)
    owner = a.space.split("/")[0]
    api.create_repo(a.space, repo_type="space", space_sdk="docker", private=not a.public, exist_ok=True)
    api.update_repo_settings(a.space, repo_type="space", private=not a.public)
    api.add_space_secret(a.space, "GEMINI_API_KEY", gemini)
    api.add_space_secret(a.space, "DEMO_SALT", os.urandom(16).hex())
    for k, v in VARIABLES.items():
        api.add_space_variable(a.space, k, v)
    with tempfile.TemporaryDirectory() as tmp:
        build(Path(tmp))
        api.upload_folder(folder_path=tmp, repo_id=a.space, repo_type="space", commit_message="Deploy DocIntel demo",
                          delete_patterns=["docintel/**", "samples/**"])
    print(f"Uploaded. Building: https://huggingface.co/spaces/{a.space}  (author shown as {owner})")


if __name__ == "__main__":
    main()
