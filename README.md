# FilingLens

**Ask Indian stock filings anything. Every answer cites the exact PDF page. A deterministic red-flag score sits beside it.**

Built solo for Arthaton 2026 (AI for finance).

## What it does

1. **Ingests** public NSE/BSE filings for a fixed universe of stocks: quarterly results, earnings-call transcripts, annual reports, shareholding patterns and pledge disclosures, auditor/other corporate announcements.
2. **Answers questions** ("why did margins fall in Q2 FY26?", "has the promoter pledged shares?") with hybrid retrieval (BM25 + pgvector) and an LLM that must return JSON citations. A **verifier** checks that every cited quote exists verbatim in the cited chunk; unsupported claims are dropped and marked.
3. **Scores red flags** deterministically from structured data — pledge creep, auditor change, guidance misses, related-party spikes, promoter selling, low-float pumps — each linked to its source filing.
4. **Reports evaluation numbers** on a 50-question hand-labelled set (answer accuracy, citation precision, abstention on unanswerable questions).

Not a trading tool. No orders, no broker keys, no real money. Research and investor-protection only.

## Run

```bash
cp .env.example .env            # add LLM_API_KEY
docker compose up -d db          # Postgres 16 + pgvector
pip install -r requirements.txt
python -m scripts.download_data  # fetches frozen public filings corpus
python -m ingest.run             # parse → chunk → embed → index
uvicorn api.main:app --reload    # http://localhost:8000
streamlit run web/app.py         # http://localhost:8501
python -m eval.run_eval          # prints the eval table
```

## Architecture

```
PDF → parse (pymupdf, OCR fallback) → section-aware chunks → embeddings + BM25
query → hybrid retrieve (top-40) → rerank (top-8) → LLM JSON answer → citation verifier
shareholding / announcements → flags.py (pure rules) → red-flag score
```

## Evaluation

| Metric | Value |
|---|---|
| Answer accuracy (40 answerable Qs) | _TBD_ |
| Citation precision | _TBD_ |
| Abstention on 10 unanswerable Qs | _TBD_ |

## Models and libraries

- LLM: _TBD_ (Gemini / Claude via API; set in `.env`)
- Embeddings: `BAAI/bge-m3` (sentence-transformers)
- Reranker: `BAAI/bge-reranker-base`
- pymupdf, rank_bm25, pgvector, FastAPI, Streamlit

## Related work and disclosure

- Filing-ingestion code (announcement polling, PDF download) adapted from earlier personal work on a separate project; everything else written for Arthaton.
- Transcript summarisers exist for US/HK/A-share markets (e.g. BeeBee AI). FilingLens differs in India-specific filing types, verified page-level citations, deterministic risk flags, and published evaluation.

## Limitations

- Universe is ~50 stocks, last 3 years, frozen offline. Not live.
- Concall transcripts are filed inconsistently before 2022.
- Red flags are heuristics, not advice. Thresholds are stated in `core/flags.py`.
- LLM answers can still be wrong even when citations verify; the verifier checks quote presence, not reasoning.
