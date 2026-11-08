"""Start the app on your own computer: `docintel` (or `python -m docintel`).

Reads .env if present, checks that Ollama is running and the model is pulled,
then serves the console on http://localhost:8030 and opens it in the browser.
Your documents stay on this machine: uploads, the database and the CSVs all
live in ./data.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

import httpx

DEFAULT_MODEL = "docintel-qwen1.5b-ft-v5"


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def check_ollama(model: str) -> str | None:
    """None when the model is ready, otherwise what the user should do."""
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=3).json().get("models", [])
    except httpx.HTTPError:
        return f"Ollama is not running at {base}. Install it from https://ollama.com and start it."
    names = {t["name"] for t in tags} | {t["name"].removesuffix(":latest") for t in tags}
    if model not in names:
        return f"Model '{model}' is not in Ollama yet. See docs/USER_GUIDE.md, step 2."
    return None


def main() -> None:
    ap = argparse.ArgumentParser(prog="docintel", description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8030")))
    ap.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps the app private to this computer")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    load_env()
    os.environ.setdefault("EXTRACTOR", f"ollama:{DEFAULT_MODEL}")
    spec = os.environ["EXTRACTOR"]
    if spec.startswith(("ollama:", "cascade:ollama:")):
        model = spec.removeprefix("cascade:").removeprefix("ollama:").split("|")[0]
        problem = check_ollama(model)
        if problem:
            print(f"docintel: {problem}", file=sys.stderr)
            sys.exit(1)
    print(f"docintel: extractor {spec}")
    url = f"http://localhost:{a.port}"
    print(f"docintel: open {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(1.5, webbrowser.open, (url,)).start()

    import uvicorn
    uvicorn.run("docintel.api.main:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
