"""Phase 0: generate the synthetic dataset and freeze splits.

    python scripts/make_data.py --cases 60 --invoices 60
"""
import argparse
import json
from pathlib import Path

from docintel.synth.generator import generate
from docintel.synth.split import freeze_splits

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="data/synthetic")
ap.add_argument("--cases", type=int, default=60)
ap.add_argument("--invoices", type=int, default=60)
ap.add_argument("--seed", type=int, default=42)
a = ap.parse_args()

out = Path(a.out)
cases = generate(out, a.cases, a.invoices, seed=a.seed)
m = freeze_splits(out)
print(f"{len(cases)} cases, {sum(1 for c in cases.values() if c['inconsistent'])} with seeded inconsistencies")
print("test sha256:", m["test_sha256"][:16])
print(json.dumps(m["counts"], indent=2))
