"""Download the fine-tuned model and register it with Ollama (one time, ~1.6 GB).

    python scripts/get_model.py

Fetches the GGUF from Hugging Face into models/, then runs `ollama create` with
the project's Modelfile so the prompt format and 8K context match training.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import httpx

REPO = "sivram04/docintel-qwen1.5b-ft-v5-GGUF"
FILE = "model-q8_0.gguf"
NAME = "docintel-qwen1.5b-ft-v5"


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length", 0)), 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done / 1e9:.2f} / {total / 1e9:.2f} GB", end="", flush=True)
    print()
    tmp.replace(dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--name", default=NAME)
    a = ap.parse_args()

    dest = Path("models") / a.name / FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"Already downloaded: {dest}")
    else:
        print(f"Downloading {a.repo}/{FILE}")
        download(f"https://huggingface.co/{a.repo}/resolve/main/{FILE}", dest)

    modelfile = Path("training/Modelfile").read_text(encoding="utf-8").splitlines()
    modelfile = [f"FROM ./{FILE}" if line.startswith("FROM ") else line for line in modelfile]
    (dest.parent / "Modelfile").write_text("\n".join(modelfile) + "\n", encoding="utf-8")
    try:
        subprocess.run(["ollama", "create", a.name, "-f", "Modelfile"], cwd=dest.parent, check=True)
    except FileNotFoundError:
        sys.exit("Ollama is not installed: get it from https://ollama.com, then rerun this script.")
    print(f"Ready. Start the app with: docintel   (model: {a.name})")


if __name__ == "__main__":
    main()
