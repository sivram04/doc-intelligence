"""Phase 0: generate the synthetic dataset and freeze splits.

    python scripts/make_data.py --cases 60 --invoices 60 --scan-rate 0.4
"""
import argparse
import json
from pathlib import Path

from docintel.synth.generator import generate

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="data/synthetic")
ap.add_argument("--cases", type=int, default=60)
ap.add_argument("--invoices", type=int, default=60)
ap.add_argument("--scan-rate", type=float, default=0.4)
ap.add_argument("--seed", type=int, default=42)
a = ap.parse_args()

out = Path(a.out)
cases = generate(out, a.cases, a.invoices, scan_rate=a.scan_rate, seed=a.seed)
m = json.loads((out / "splits.json").read_text())
print(f"{len(cases)} cases, {sum(1 for c in cases.values() if c['inconsistent'])} with seeded inconsistencies")
print("test sha256:", m["test_sha256"][:16])
print(json.dumps(m["counts"], indent=2))
