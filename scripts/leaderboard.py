"""Model race leaderboard (Phase 3): collects reports/eval_*_<split>.json and
writes reports/leaderboard.md plus a field-F1-by-type chart for the README.

    python scripts/leaderboard.py
"""
import argparse
import json
from pathlib import Path

# Reference categorical palette (fixed order, validated for CVD separation).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, TEXT, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
TYPES = ["w2", "paystub", "bank_statement", "invoice"]


def overall_f1(r: dict) -> float:
    s = r["field_scores_by_type"]
    n = sum(v["fields"] for v in s.values())
    return sum(v["f1"] * v["fields"] for v in s.values()) / n if n else 0.0


def fmt(x, nd=2, suffix=""):
    return "n/a" if x is None else f"{x:.{nd}f}{suffix}"


def chart(rows: list[dict], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    n = len(rows)
    width = 0.8 / n
    for i, r in enumerate(rows):
        xs = [t + (i - (n - 1) / 2) * width for t in range(len(TYPES))]
        ys = [r["field_scores_by_type"].get(t, {}).get("f1", 0) for t in TYPES]
        ax.bar(xs, ys, width * 0.92, color=SERIES[i % len(SERIES)], label=r["model"], zorder=2)
    ax.set_xticks(range(len(TYPES)), [t.replace("_", " ") for t in TYPES], color=TEXT)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Field F1 (test, held-out labels)", color=MUTED)
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis="y", color=GRID, zorder=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.legend(frameon=False, labelcolor=TEXT, ncols=min(n, 4), loc="upper center", bbox_to_anchor=(0.5, 1.15))
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--split", default="test")
    a = ap.parse_args()

    rows = [json.loads(p.read_text()) for p in sorted(Path(a.reports).glob(f"eval_*_{a.split}.json"))]
    rows.sort(key=overall_f1, reverse=True)
    lines = [
        f"# Model race leaderboard ({a.split} split)\n",
        ("| Model | Field F1 | W-2 | Pay stub | Bank | Invoice | Native F1 | Scan F1 | Review rate "
         "| p95 latency (s) | Tokens in/out per doc | Cost per 1K docs (USD) |"),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        bt, bm = r["field_scores_by_type"], r["field_scores_by_modality"]
        tok = r.get("tokens_per_doc", {})
        lines.append("| " + " | ".join([
            r["model"], fmt(overall_f1(r)), *(fmt(bt.get(t, {}).get("f1")) for t in TYPES),
            fmt(bm.get("native", {}).get("f1")), fmt(bm.get("scan", {}).get("f1")), fmt(r["review_rate"]),
            fmt(r["p95_latency_ms"] / 1000, 1), f"{tok.get('input', 0)}/{tok.get('output', 0)}",
            fmt(r.get("cost_per_1k_docs_usd"), 2),
        ]) + " |")
    lines.append(f"\n{rows[0]['documents'] if rows else 0} test documents. Latency is wall-clock per document "
                 "on the machine that ran the eval; cost is at paid list prices (free tiers cost $0).")
    out = Path(a.reports) / "leaderboard.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if rows:
        chart(rows, Path(a.reports) / "leaderboard.png")
    print(out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
