# FilingLens

**Ask Indian stock filings anything. Every answer cites the exact PDF page. A deterministic red-flag score sits beside it.**

Built solo for Arthaton 2026 (AI for finance).

## What it does

1. **Ingests** public NSE/BSE filings for a fixed universe of stocks: quarterly results, earnings-call transcripts, annual reports, shareholding patterns and pledge disclosures, auditor/other corporate announcements.
2. **Answers questions** ("why did margins fall in Q2 FY26?", "has the promoter pledged shares?") with hybrid retrieval (BM25 + dense vectors) and an LLM that must return JSON citations. A **verifier** checks that every cited quote exists verbatim in the cited chunk; unsupported claims are dropped and marked.
3. **Scores red flags** deterministically, with no LLM in the loop — promoter encumbrance, promoter stake drops, auditor resignations, modified audit opinions, low-float price spikes — each linked to the filing (and page) it came from. See [Red flags](#red-flags).
4. **Reports evaluation numbers** on a 50-question hand-labelled set (answer accuracy, citation precision, abstention on unanswerable questions).

Not a trading tool. No orders, no broker keys, no real money. Research and investor-protection only.

## Run

```bash
cp .env.example .env            # add LLM_API_KEY
pip install -r requirements.txt  # Python 3.12; storage is SQLite + a flat numpy index, no server
python -m scripts.selftest       # offline end-to-end check (set EMBED_BACKEND=hash to skip torch)
python -m scripts.download_data  # fetches frozen public filings corpus
python -m ingest.run             # parse → chunk → embed → index
python -m ingest.shareholding    # NSE quarterly shareholding XBRL → facts (cached under data/raw)
python -m ingest.auditor         # auditor resignations + modified opinions → facts
python -m ingest.market          # prices (cached) + public float → facts
uvicorn api.main:app --reload    # http://localhost:8000
streamlit run web/app.py         # http://localhost:8501
python -m eval.run_eval          # prints the eval table
```

## Architecture

```
PDF → parse (pymupdf, OCR fallback) → section-aware chunks → embeddings + BM25
storage: SQLite (docs, chunks, facts) + flat float32 vector file (cosine by one matmul)
query → quarter filter ("Q2 FY27") → hybrid retrieve (top-40) → top-8 → LLM JSON answer → citation verifier
NSE shareholding XBRL + BSE announcements/audit reports + prices → facts → flags.py (pure rules) → red-flag score
```

## Red flags

Rules only; every flag carries its evidence (filing, page, quote or the filed numbers). Thresholds live in `core/flags.py` and are reported here as-is.

| Flag | Fires when | Source |
|---|---|---|
| `pledge_creep` | encumbered share of promoter holding up ≥ 10pp over 4 quarters | NSE shareholding pattern (XBRL) |
| `pledge_high` | encumbered share of promoter holding ≥ 50% | same |
| `promoter_stake_drop` | promoter holding down ≥ 3pp in one quarter, or ≥ 5pp over 12 quarters | same |
| `auditor_change` | statutory auditor (or a subsidiary's) resigns / casual vacancy | BSE announcements, rules over title + body |
| `audit_qualification` | a results filing contains an SA 705 "Basis for Qualified / Adverse / Disclaimer of Opinion" | audit reports inside BSE results PDFs |
| `float_pump` | max 3-month return in the last year ≥ 100% with public float < 25% | Yahoo Finance prices + NSE public % |

"Encumbered" follows SEBI's definition: pledge + non-disposal undertakings + other encumbrances. The flag text shows the split, because it matters — VEDL's promoter holding is ~100% encumbered with **0% pledged** (bond covenants), which a pledge-only check would miss.

Results on the 44-symbol universe: 17 symbols flagged; none of the 13 large-cap controls fires anything.

## Evaluation

| Metric | Value |
|---|---|
| Answer accuracy (40 answerable Qs) | _TBD_ |
| Citation precision | _TBD_ |
| Abstention on 10 unanswerable Qs | _TBD_ |

## Models and libraries

- LLM: `gemini-3.5-flash-lite` (fallbacks `gemini-3.5-flash`, `gemini-2.5-flash-lite`; set in `.env`)
- Embeddings: `BAAI/bge-small-en-v1.5` (sentence-transformers, 384-dim, CPU)
- Reranker: `BAAI/bge-reranker-base`, off by default (`RERANK=1` to enable); see the ablation
- pymupdf, rank_bm25, numpy, SQLite, FastAPI, Streamlit, yfinance

## Related work

- Transcript summarisers exist for US/HK/A-share markets (e.g. BeeBee AI). FilingLens differs in India-specific filing types, verified page-level citations, deterministic risk flags, and published evaluation.

## Limitations

- Universe is 44 stocks, filings since April 2023, frozen offline (2,924 PDFs). Not live.
- Q&A covers a 12-stock demo subset and skips annual reports (CPU embedding at ~9 chunks/s); flags cover all 44.
- Some companies never file call transcripts (e.g. ITC, RCOM, PCJEWELLER), so their Q&A rests on results and announcements.
- ~4.7% of PDF pages are images; without Tesseract installed they are indexed as empty.
- Red flags are heuristics, not advice. `promoter_stake_drop` cannot tell selling from dilution (IDEA's drop is a government debt-to-equity conversion). `float_pump` uses Yahoo Finance prices.
- `rpt_spike` (related-party transactions) needs annual reports and is not built; `guidance_miss` was cut because managements mostly guide qualitatively, and a wrong flag citing a real page would undermine the citations.
- The quarter filter only applies when a question names a quarter ("Q2 FY27"); "latest quarter" searches all.
- LLM answers can still be wrong even when citations verify; the verifier checks quote presence, not reasoning.
