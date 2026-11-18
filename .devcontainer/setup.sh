#!/usr/bin/env bash
# One-time setup for a Codespace (also what a prebuild runs): dependencies, demo data, search models.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== FilingLens: installing dependencies (CPU-only PyTorch, no GPU libraries)"
# Network hiccups to PyPI happen (a 15 s read timeout failed one test build): long timeouts, pip's own
# retries, and the whole step retried up to 3 times.
PIP="pip install --no-cache-dir --quiet --timeout 60 --retries 10"
for attempt in 1 2 3; do
  $PIP torch --index-url https://download.pytorch.org/whl/cpu && $PIP -r requirements.txt && break
  [ "$attempt" = 3 ] && { echo "dependency install failed 3 times"; exit 1; }
  echo "install attempt $attempt failed; retrying in 15 s"; sleep 15
done

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
