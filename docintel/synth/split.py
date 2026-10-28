"""Frozen train/val/test splits (Phase 0). Assignment is by group (a case or a
standalone invoice) so a person's documents never leak across splits, and is
a pure hash of (seed, group id) so it is stable when the dataset grows. The
test manifest is hashed so any later change to it is detectable."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

TEST_FRAC, VAL_FRAC = 0.25, 0.15


def assign_split(group_id: str, seed: int) -> str:
    u = int(hashlib.sha256(f"{seed}:{group_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if u < TEST_FRAC else "val" if u < TEST_FRAC + VAL_FRAC else "train"


def write_manifest(data_dir: Path, assignment: dict[str, str], seed: int) -> dict:
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    counts: dict[str, Counter] = {k: Counter() for k in splits}
    for doc_id in sorted(assignment):
        s = assignment[doc_id]
        gt = json.loads((data_dir / "docs" / f"{doc_id}.json").read_text())
        splits[s].append(doc_id)
        counts[s][f"{gt['doc_type']}/{gt['modality']}"] += 1
    test_hash = hashlib.sha256("\n".join(splits["test"]).encode()).hexdigest()
    manifest = {"seed": seed, "test_sha256": test_hash, "splits": splits,
                "counts": {k: dict(sorted(v.items())) for k, v in counts.items()}}
    (data_dir / "splits.json").write_text(json.dumps(manifest, indent=2))
    return manifest
