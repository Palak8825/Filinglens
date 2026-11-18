#!/usr/bin/env bash
# One-time setup for a Codespace (also what a prebuild runs): dependencies, demo data, search models.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== FilingLens: installing dependencies (CPU-only PyTorch, no GPU libraries)"
pip install --no-cache-dir --quiet torch --index-url https://download.pytorch.org/whl/cpu
pip install --no-cache-dir --quiet -r requirements.txt

echo "== FilingLens: downloading the demo data from the GitHub release"
REL="https://github.com/Palak8825/Filinglens/releases/download/v1.0-data"
DL="/tmp/filinglens-demo"
mkdir -p "$DL"
curl -fsSL --retry 5 -o "$DL/SHA256SUMS-demo.txt" "$REL/SHA256SUMS-demo.txt"
curl -fsSL --retry 5 -C - -o "$DL/filinglens-demo.zip" "$REL/filinglens-demo.zip"
python -m scripts.setup_release "$DL" --demo   # verifies the checksum, then unpacks into data/
rm -rf "$DL"

echo "== FilingLens: caching the search models (so the first question isn't slow)"
python - <<'PY'
from sentence_transformers import CrossEncoder, SentenceTransformer
from core.config import settings
SentenceTransformer(settings.embed_model)
CrossEncoder(settings.rerank_model)
PY
echo "== FilingLens: setup done"
