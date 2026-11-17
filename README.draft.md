# FilingLens

**Ask questions about the filings of Indian listed companies. Every claim in an answer cites the exact PDF page, and code checks each citation before you see it. A rule-based red-flag score sits next to the answer.**

Built solo for Arthaton 2026 (AI for personal and business finance). Demo video: _<YouTube link>_

FilingLens answers questions over public NSE/BSE filings (quarterly results, earnings-call transcripts, announcements, shareholding patterns). Retrieval is hybrid: BM25 plus dense embeddings, fused with Reciprocal Rank Fusion and reranked by a cross-encoder. The LLM has to back every claim with a verbatim quote from a numbered excerpt. Code, not the model, then checks that each quote really appears in the excerpt it points to. Citations that fail are dropped and counted, and each one that passes links to the rendered PDF page with the quote highlighted. Next to the answers, a rules-only engine (no LLM) scores governance red flags from shareholding XBRL, auditor filings and prices. An evaluation harness measures retrieval, citation precision, abstention and accuracy, and the answers are graded by a judge model that never grades its own output.

It is not a trading tool. There are no orders, no broker keys, no portfolio and no money. It is for research and investor protection only.

## What it does

| Page | What you see |
|---|---|
| **Landing** | A splash screen with one way in. |
| **Home** | Corpus overview, plus one example: a verified answer with its quote highlighted on the source page. |
| **Dashboard** | A risk screen of all 44 stocks with a score, a level and the flags behind them. Charts show the score by stock, how often each rule fires, and promoter holding and encumbrance over time. Clicking a flag opens its **detail page**: the rule and its threshold, what was found, the filed numbers, the evidence pages, a link to open the PDF at the cited page, the original filing on BSE, and NSE's shareholding page (with the raw XBRL files labelled as machine-readable data). |
| **Ask** | Pick a stock, then ask a question or click an example. The answer shows its verified-citation count and a "How this answer was checked" strip (passages found, quotes given, confirmed or rejected). Each citation gets a tab with the quote highlighted on the rendered PDF page and a **Verify it yourself** box: open the PDF at the cited page, see the original filing on BSE, and copy the exact quote to search for. When a date or relative period was resolved, a note shows how it was read. |
| **Evaluation** | The question mix, the headline metrics, the retrieval ablation, and per-configuration tables, read from `eval/report.json`. |

The sidebar moves between Home, Dashboard, Ask and Evaluation.

## Quick start

The [Releases](https://github.com/Palak8825/Filinglens/releases) page has two download options. `scripts/setup_release.py` checks every zip against the checksum file before it extracts anything into `./data`.

| Download | What it contains | Setup |
|---|---|---|
| `filinglens-demo.zip` + `SHA256SUMS-demo.txt` (~500 MB) | Q&A index for 5 stocks (IDEA, PAYTM, RELIANCE, VEDL, YESBANK). It includes every PDF the example answers cite, each stock's latest two quarters of results and transcripts, the auditor-flag evidence, all red-flag facts (44 stocks), and the corpus listing. | `python -m scripts.setup_release <folder> --demo` |
| `filinglens-index.zip` + `filinglens-pdfs-part1.zip` + `filinglens-pdfs-part2.zip` + `SHA256SUMS.txt` | Full index (14 stocks, 929 filings, 41,219 chunks), cached shareholding XBRL and prices, and every PDF the app can cite. Each PDF part is under 1.9 GB. | `python -m scripts.setup_release <folder>` |

```bash
git clone https://github.com/Palak8825/Filinglens.git && cd Filinglens
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m scripts.setup_release path/to/downloads --demo   # or without --demo for the full release
uvicorn api.main:app                                      # http://localhost:8000
streamlit run web/app.py                                  # http://localhost:8501 (second terminal)
```

**Without an API key**, the red flags, the dashboard, the flag detail pages, the cited page images and the example answers all work. Asking a question that isn't cached returns a message saying a key is needed.

**With a key** (`cp .env.example .env`, then set `LLM_API_KEY`; the Gemini free tier works), you can ask anything about the indexed stocks. The **Ask live** toggle regenerates an example question live.

`python -m scripts.selftest` runs offline in seconds against a separate `data/selftest/` database. It checks chunking (including the transcript fix below), retrieval, period resolution, the citation verifier, the flags, the answer cache and eval cell formatting.

### Cached example answers (disclosure)

`demo/answer_cache.json` holds answers to the 11 questions in `demo/questions.json`, covering 5 stocks. `python -m scripts.warm_cache` generated them with the same pipeline the app uses live: period resolution, hybrid retrieval, rerank, LLM, citation verifier. Each entry is stored whole: the answer, the verified citations, the dropped-citation count, the model that actually answered (some came from a fallback model), the generation date, and the index size.

- In the UI, a cached answer carries an **⚡ Example answer** label.
- Only `warm_cache` writes to the cache. Live questions are never added to it.
- **Questions that aren't in the cache are always answered live.** With a key set, a cached entry is used only when it was generated with the configured `LLM_MODEL`.
- The cache exists so that a reviewer without a key still sees real pipeline output, and so a demo survives a free-tier outage.

## How it works

```
PDF ─ pymupdf (Tesseract OCR fallback if installed) ─ section/speaker-aware chunks, one page each ─┬─ BM25
                                                                                                  └─ bge-small embeddings → flat float32 file
question ─ period resolver ─ hybrid retrieve top-40 (BM25 + dense, RRF) ─ cross-encoder rerank top-8
         ─ LLM → JSON {answer, citations[{ref, quote}], confidence} ─ citation verifier ─ answer + highlighted page images
NSE shareholding XBRL + BSE announcements / audit reports + prices ─ facts ─ core/flags.py (rules) ─ risk score
```

- **Chunking** (`ingest/chunk.py`): chunks are about 450 tokens and never cross a page, so every chunk has exactly one page number. Transcripts are split by speaker, and adjacent utterances are merged so an analyst's question stays with its answer.
- **Period resolver** (`core/retrieve.py`, no LLM call): it handles explicit quarters ("Q2 FY27", "Q3 FY26 vs Q2 FY27", which keeps every named quarter), quarter-end dates ("as of 30 September 2026" maps to Q2 FY27), and relative phrases ("latest quarter" maps to the newest quarter filed for that stock; adding "previous quarter" or "QoQ" brings in the quarter before it). The result is a hard filter on fiscal period. If that period has no filings, retrieval falls back to all filings. The UI shows how a date or relative phrase was interpreted.
- **Verifier** (`core/verify.py`): a quote survives only if it appears in the cited chunk, either as an exact substring or with a rapidfuzz `partial_ratio` of at least 92 for quotes of 20 characters or more. Shorter quotes must match exactly and contain both a word and a number. If the model cited something but nothing verified, the answer is prefixed "Unverified" and its confidence is set to low.
- **Storage:** SQLite plus a flat numpy vector file. At 41k chunks × 384 dimensions, cosine similarity is one matrix multiply on CPU, and no vector database or RAG framework is involved.

## AI models and APIs used

| Role | Model / API | Config (`core/config.py`, `.env.example`) | Notes |
|---|---|---|---|
| Answer generation | Google Gemini API, `gemini-3.5-flash-lite` | `LLM_MODEL` | JSON response mode, temperature 0, 60 s timeout. The model that actually answered is recorded in each answer. |
| Fallbacks | `gemini-3.5-flash`, then `gemini-3.1-flash-lite` | `LLM_FALLBACK_MODELS` | Used only when the model returns 429/500/503/504. `gemini-2.5-*` models are retired for new keys, so they are not used. Fallback is off during eval runs (one model per arm). |
| Eval judge | Gemini API, `gemini-3.6-flash` | `JUDGE_MODEL` | Different from every answering model. `eval/run_eval.py` warns if an arm would answer with the judge model. |
| Eval weak-model arm | `gemini-3.1-flash-lite` | `hybrid+rerank@weak` | Gives the verifier something to reject. |
| Eval model arm | `gemini-3.5-flash` | `hybrid+rerank@flash` | Same pipeline with a larger model. |
| Embeddings | `BAAI/bge-small-en-v1.5` (sentence-transformers, local CPU) | `EMBED_MODEL`, `EMBED_DIM=384` | |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local CPU) | `RERANK_MODEL`, `RERANK=1` | `RERANK=0` disables it. |
| Data | BSE corporate announcements, NSE shareholding-pattern XBRL, Yahoo Finance prices (`yfinance`) | | Fetched once and frozen. Nothing is fetched at query time. |

An Anthropic provider exists as an alternative (`LLM_PROVIDER=anthropic`, which uses `LLM_MODEL` with no fallback chain). It was not used for any reported numbers. Libraries: pymupdf, pytesseract, rank_bm25, numpy, SQLAlchemy/SQLite, rapidfuzz, FastAPI, Streamlit/Altair, pandas. There is no LangChain or LlamaIndex.

## Red flags

Rules only. Every flag carries its evidence: the filing, the page, and the quote or the filed numbers. Thresholds come from `THRESHOLDS` in `core/flags.py`, and the UI prints them.

| Flag | Fires when | Severity | Source |
|---|---|---|---|
| `pledge_creep` | Encumbered share of promoter holding up ≥ 10pp over 4 quarters | 3 | NSE shareholding pattern (XBRL) |
| `pledge_high` | Encumbered share of promoter holding ≥ 50% | 2 | same |
| `promoter_stake_drop` | Promoter holding down ≥ 3pp in one quarter (last 4), or ≥ 5pp over 12 quarters | 2 | same |
| `auditor_change` | Statutory auditor resigns (3) or a subsidiary's auditor resigns (2) | 2–3 | BSE announcements |
| `audit_qualification` | A results filing contains a qualified, adverse or disclaimer opinion | 3 | Audit reports inside BSE results PDFs |
| `float_pump` | Largest 3-month return in the last year ≥ 100% with public float < 25% | 3 | Yahoo Finance prices + NSE public % |

Score = sum of severities. The level is **high** at 5 or more, **elevated** at 2 or more, and **low** otherwise. "Encumbered" follows SEBI's definition (pledge + non-disposal undertakings + other encumbrance), and the flag text shows the split. For example, VEDL is 100% encumbered with 0% pledged (95% "other"), which a pledge-only check would miss.

On the current data, 17 of 44 stocks are flagged. The other 27 fire nothing, including large caps such as HDFCBANK, INFY and TCS.

## Evaluation

30 hand-labelled questions over 10 stocks (`eval/questions.jsonl`): 9 factual, 4 reasoning, 5 cross-doc, 2 flag and 10 unanswerable. Each answerable question lists its gold (document, page) pairs.

- **Retrieval ablation:** BM25, dense, hybrid, and hybrid + rerank, scored by hit@8 (a gold page is among the 8 excerpts the LLM reads) and MRR. A **cross-doc** question counts as a success only if a gold document from **every** fiscal period it spans is retrieved.
- **End to end, per arm:** answer accuracy (LLM judge against the gold answer, plus a deterministic `must_include` check where one is given), citation precision with and without the verifier, verifier rejection rate, correct abstention on unanswerable questions, and false abstention.
- **Arms:** `hybrid+rerank` (production), `hybrid`, `hybrid+rerank@flash` (larger model), `hybrid+rerank@weak` (`gemini-3.1-flash-lite`), and `hybrid+rerank@v2` (a relaxed prompt for "why" questions).
- **Judge:** `gemini-3.6-flash`, which differs from every answering model. Verdicts from an older judge are re-graded without re-answering. If the judge is unavailable, the answer is kept and marked pending, and the next run grades it, so no answers are lost and accuracy is computed over graded answers only.
- Any cell over fewer than 5 questions prints counts (`k/n`), not percentages.

<eval tables: paste from eval/report.md after the final run>

Reproduce: `python -m eval.run_eval --configs hybrid+rerank,hybrid,hybrid+rerank@flash,hybrid+rerank@weak,hybrid+rerank@v2`. Answers are cached under `eval/cache/`. The run writes `eval/report.md` and `eval/report.json`.

## Limitations

- **Verified isn't correct.** The verifier checks that a quote is present in the cited chunk. It does not check the reasoning built on that quote.
- **Filer-OCR noise.** Some filings carry a text layer made by the company's own scanner software. A heuristic (`python -m scripts.ocr_noise`) finds likely OCR misreadings (e.g. "crorc" for "crore") on about **3.3% of indexed pages** (611 of 18,707) and **4.2% of results pages**. The verifier cannot catch these, because it checks that the text is present, not that it is true.
- **Image-only pages.** About 4.7% of PDF pages have no text layer. Tesseract OCR runs only if it is installed. In the shipped index these pages are not indexed, so they can't be retrieved or cited.
- **Small-model refusals.** `gemini-3.5-flash-lite` sometimes says "not found" on cross-quarter comparisons even when both figures are in its excerpts. The `@flash` arm in the eval measures this.
- **Frozen corpus.** 44 stocks and 2,924 filings since April 2023, frozen offline. It is not live.
- **Coverage.** Q&A covers 14 stocks and skips annual reports (because of CPU embedding cost). Flags cover all 44. The demo download indexes 5 stocks.
- **Heuristic flags.** The flags are not advice. `promoter_stake_drop` can't tell selling from dilution: IDEA's drop (50.36% to 25.64%) comes from equity issued to the Government of India, which one of the example answers explains with citations. `float_pump` relies on Yahoo Finance prices. `rpt_spike` exists in code but has no data, because it needs annual reports.

## Engineering notes: bugs found and fixed

- **Transcript text was being dropped.** The speaker splitter discarded text that came before the first speaker label on a transcript page, which is usually the previous page's speaker still talking. In a 40-transcript sample, that was about 12% of transcript text, and 38% of pages with speaker labels started mid-speech. `ingest/chunk.py` now attributes that text to the last speaker from the previous page, and `scripts/selftest.py` guards the fix. Transcripts were re-chunked in place with `python -m ingest.run --rechunk concall`, which took transcript chunks from 8,294 to 9,498.
- **Cross-doc success was unreachable.** Counting distinct gold files failed because the corpus holds byte-identical duplicate filings and retrieval de-duplicates identical text. Success is now judged per fiscal period: at least one gold document from each period the question spans.
- **Eval judge outages lost work.** Grading is now deferred instead of discarding the answer (see Evaluation).

## Related work

Transcript summarisers exist for US, HK and A-share markets (e.g. BeeBee AI). FilingLens differs in its India-specific filing types, page-level citations verified in code, deterministic risk flags, and a published evaluation.
