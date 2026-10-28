"""Freeze train/val/test splits (Phase 0). Split is by case so a person's
documents never leak across splits. The test manifest is hashed so any later
change to it is detectable."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path


def group_key(doc_id: str) -> str:
    return doc_id.split("-")[0]


def freeze_splits(data_dir: Path, test_frac=0.25, val_frac=0.15, seed=7) -> dict:
    gts = sorted((data_dir / "docs").glob("*.json"))
    groups = sorted({group_key(p.stem) for p in gts})
    random.Random(seed).shuffle(groups)
    n_test, n_val = int(len(groups) * test_frac), int(len(groups) * val_frac)
    assign = {g: "test" for g in groups[:n_test]}
    assign |= {g: "val" for g in groups[n_test:n_test + n_val]}
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    counts: dict[str, Counter] = {k: Counter() for k in splits}
    for p in gts:
        s = assign.get(group_key(p.stem), "train")
        splits[s].append(p.stem)
        counts[s][json.loads(p.read_text())["doc_type"]] += 1
    test_hash = hashlib.sha256("\n".join(splits["test"]).encode()).hexdigest()
    manifest = {"seed": seed, "test_sha256": test_hash, "splits": splits,
                "counts": {k: dict(v) for k, v in counts.items()}}
    (data_dir / "splits.json").write_text(json.dumps(manifest, indent=2))
    return manifest
