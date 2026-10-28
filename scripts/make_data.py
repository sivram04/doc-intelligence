"""Phase 0: generate the synthetic dataset and freeze splits.

    python scripts/make_data.py --cases 60 --invoices 60 --scan-rate 0.4
"""
import argparse
import json
from pathlib import Path

from docintel.synth.generator import generate
from docintel.synth.generator_v2 import generate_v2

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="data/synthetic")
ap.add_argument("--cases", type=int, default=60)
ap.add_argument("--invoices", type=int, default=60)
ap.add_argument("--scan-rate", type=float, default=0.4)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--benchmark", choices=["v1", "v2"], default="v1", help="v2: realistic layouts, tables, held-out layout family")
a = ap.parse_args()

out = Path(a.out)
gen = generate_v2 if a.benchmark == "v2" else generate
cases = gen(out, a.cases, a.invoices, scan_rate=a.scan_rate, seed=a.seed)
m = json.loads((out / "splits.json").read_text())
print(f"{len(cases)} cases, {sum(1 for c in cases.values() if c['inconsistent'])} with seeded inconsistencies")
print("test sha256:", m["test_sha256"][:16])
print(json.dumps(m["counts"], indent=2))
