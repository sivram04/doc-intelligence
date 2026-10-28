#!/usr/bin/env bash
# Phase 4: push the SFT dataset to Kaggle (private) and launch the QLoRA job on a free T4.
#   bash scripts/kaggle_train.sh            # upload data (new version if it exists) + start training
#   bash scripts/kaggle_train.sh status     # job status
#   bash scripts/kaggle_train.sh fetch      # download outputs to models/finetuned/
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${KAGGLE_API_TOKEN:?KAGGLE_API_TOKEN missing in .env}"
USER_NAME="${KAGGLE_USERNAME:-ramv44444}"
DATASET="$USER_NAME/docintel-sft"
KERNEL="$USER_NAME/docintel-qlora"
KG="python -m kaggle"
STAGE="$(pwd)/build/kaggle"
# The Kaggle CLI mangles POSIX paths on Windows; hand it native ones.
winpath() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else echo "$1"; fi; }

case "${1:-train}" in
  status) $KG kernels status "$KERNEL"; exit ;;
  fetch)
    mkdir -p models/finetuned
    $KG kernels output "$KERNEL" -p models/finetuned
    ls -la models/finetuned; exit ;;
esac

rm -rf "$STAGE" && mkdir -p "$STAGE/data" "$STAGE/kernel"
cp data/sft/train.jsonl data/sft/val.jsonl data/sft/stats.json "$STAGE/data/"
cat > "$STAGE/data/dataset-metadata.json" <<EOF
{"title": "docintel-sft", "id": "$DATASET", "licenses": [{"name": "CC0-1.0"}],
 "description": "Synthetic financial-document extraction examples (no real personal data)."}
EOF
if $KG datasets status "$DATASET" >/dev/null 2>&1; then
  $KG datasets version -p "$(winpath "$STAGE/data")" -m "SFT data $(git rev-parse --short HEAD)"
else
  $KG datasets create -p "$(winpath "$STAGE/data")"
fi

cp training/train_qlora.py "$STAGE/kernel/"
cat > "$STAGE/kernel/kernel-metadata.json" <<EOF
{"id": "$KERNEL", "title": "docintel-qlora", "code_file": "train_qlora.py", "language": "python",
 "kernel_type": "script", "is_private": true, "enable_gpu": true, "enable_internet": true,
 "dataset_sources": ["$DATASET"], "competition_sources": [], "kernel_sources": []}
EOF
$KG kernels push -p "$(winpath "$STAGE/kernel")"
echo "Launched. Check with: bash scripts/kaggle_train.sh status"
