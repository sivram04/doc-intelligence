"""Download the fine-tuned model and register it with Ollama (one time, ~1.6 GB).

    python scripts/get_model.py

Fetches the GGUF from Hugging Face into models/, then runs `ollama create` with
the project's Modelfile so the prompt format and 8K context match training. Also
pulls the two stock models the Reports tab uses: qwen2.5:1.5b (summaries, when no
Gemini key is set) and nomic-embed-text (search). Skip them with --no-extras.

    python scripts/get_model.py --gguf path/to/model-q8_0.gguf   # use a file you already have
"""
import argparse
import subprocess
import sys
from pathlib import Path

import httpx

REPO = "sivram04/docintel-qwen1.5b-ft-v5-GGUF"
FILE = "model-q8_0.gguf"
NAME = "docintel-qwen1.5b-ft-v5"
EXTRAS = ["qwen2.5:1.5b", "nomic-embed-text"]


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
    ap.add_argument("--gguf", type=Path, help="a GGUF file you already have, instead of downloading")
    ap.add_argument("--no-extras", action="store_true", help="skip the summary and search models")
    a = ap.parse_args()

    dest = Path("models") / a.name / FILE
    dest.parent.mkdir(parents=True, exist_ok=True)
    if a.gguf:
        if not a.gguf.is_file():
            sys.exit(f"No such file: {a.gguf}")
        dest.write_bytes(a.gguf.read_bytes()) if a.gguf.resolve() != dest.resolve() else None
    elif dest.exists():
        print(f"Already downloaded: {dest}")
    else:
        print(f"Downloading {a.repo}/{FILE}")
        try:
            download(f"https://huggingface.co/{a.repo}/resolve/main/{FILE}", dest)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            sys.exit(f"Could not download the model (HTTP {code}) from https://huggingface.co/{a.repo}.\n"
                     + ("The model repository is not public yet or the name is wrong. "
                        "If you have the GGUF file, run: python scripts/get_model.py --gguf <path>"
                        if code in (401, 403, 404) else "Check your internet connection and try again."))
        except httpx.TransportError as e:
            sys.exit(f"Could not reach Hugging Face ({e}). Check your internet connection and try again.")

    modelfile = Path("training/Modelfile").read_text(encoding="utf-8").splitlines()
    modelfile = [f"FROM ./{FILE}" if line.startswith("FROM ") else line for line in modelfile]
    (dest.parent / "Modelfile").write_text("\n".join(modelfile) + "\n", encoding="utf-8")
    try:
        subprocess.run(["ollama", "create", a.name, "-f", "Modelfile"], cwd=dest.parent, check=True)
    except FileNotFoundError:
        sys.exit("Ollama is not installed: get it from https://ollama.com, then rerun this script.")
    if not a.no_extras:
        for m in EXTRAS:
            print(f"Pulling {m} (used by the Reports tab)")
            subprocess.run(["ollama", "pull", m], check=False)
    print(f"Ready. Start the app with: docintel   (model: {a.name})")


if __name__ == "__main__":
    main()
