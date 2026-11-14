# Drop-in replacement: no Docker, no Postgres

Replace these files in your repo. **Do not touch** `ingest/announcements.py` or
`scripts/universe.csv` — your fixed BSE fetcher and 44-symbol universe stay as they are.

| File | Change |
|---|---|
| `core/config.py` | SQLite URL; embed model → `bge-small-en-v1.5`, dim 384 |
| `core/db.py` | pgvector/psycopg gone; SQLite + WAL; `embedding` column removed |
| `core/vectors.py` | **new** — flat float32 index, cosine by matmul |
| `core/retrieve.py` | vector search now goes through `core/vectors.py` |
| `ingest/embed.py` | adds `EMBED_BACKEND=hash` fallback for testing without torch |
| `ingest/run.py` | `--symbols`, `--skip-types`, `--reembed`; resumable |
| `ingest/chunk.py` | **two bug fixes, see below** |
| `scripts/selftest.py` | **new** — end-to-end test, no network or API key |
| `requirements.txt` | pgvector + psycopg removed |
| `.env.example` | SQLite defaults |
| `docker-compose.yml` | **delete it** |

## Two bugs I found by running it

1. **Chunk explosion.** `_split_fixed` emitted one near-duplicate chunk per character of
   overlap whenever a document was shorter than the 1800-char window. Two test paragraphs
   produced **322 chunks instead of 2**. On your 2,500-PDF corpus this would have inflated the
   index by orders of magnitude, wasted hours of CPU embedding, and filled every retrieval with
   duplicates. Fixed by breaking once the tail is consumed.
2. **Concall questions dropped.** Splitting strictly per speaker discarded any utterance under
   80 chars, so analyst questions vanished and the CFO's answer was retrieved with no question
   attached. Now adjacent utterances are merged into Q&A blocks, carrying one utterance of
   context across boundaries.

Both are now guarded by assertions in `scripts/selftest.py`.

## Run order

```bash
py -3.12 -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt

# 1. Prove the pipeline works without torch or an API key (~5 seconds):
set EMBED_BACKEND=hash
python -m scripts.selftest

# 2. Real run (unset the hash backend first!):
set EMBED_BACKEND=
python -m ingest.run --symbols YESBANK,ZEEL,SUZLON,RELIANCE,INFY --skip-types annual_report
uvicorn api.main:app --reload
streamlit run web/app.py
```

`EMBED_BACKEND=hash` is for plumbing tests only. It is a bag-of-words projection, not a
semantic embedding — never use it for the eval table or the demo. If you ever mix backends in
one index, run `python -c "from core import vectors; vectors.reset()"` and
`python -m ingest.run --reembed`.

## Why SQLite is fine here

~200k chunks x 384 dims x 4 bytes ≈ 300 MB. One matmul against that is well under 100 ms on
CPU — far below the LLM call it precedes. pgvector earns its keep at millions of vectors and
concurrent writers; you have neither. Say exactly this in the README if a judge asks.
