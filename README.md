# FilingLens

## Answers from Indian company filings, proven on the page

> **Every claim cites the exact PDF page, and code checks each citation before you see it.**

FilingLens answers questions about the public filings of Indian listed companies (NSE/BSE quarterly results, earnings-call transcripts, exchange announcements, shareholding patterns). It retrieves with **hybrid RAG** (BM25 + dense embeddings, fused with Reciprocal Rank Fusion, then a cross-encoder reranker), makes the LLM quote its source for every claim, and then **checks every quote in code** against the page it cites. Next to the answers sit a **rules-only red-flag engine** (no LLM) and a per-stock **Brief**, and all of it is measured by an **evaluation harness** whose judge model never grades its own output.

Built solo for **Arthaton 2026** (AI for personal and business finance).
**Demo video:** _<YouTube link>_ · **Release data:** [v1.0-data](https://github.com/Palak8825/Filinglens/releases/tag/v1.0-data)

Built with: Python · FastAPI · Streamlit · Gemini API · sentence-transformers (bge-small) · cross-encoder reranker · SQLite + a flat numpy vector index · pymupdf. No vector database, no LangChain or LlamaIndex: the retrieval path is a few hundred lines you can read end to end.

Not a trading tool: no orders, no broker keys, no portfolio, no money. Research and investor protection only.

---

## 1. What FilingLens is trying to prove

1. **Citations can be addressable.** Every chunk keeps its page number, so a citation opens the exact page, with the quoted line highlighted.
2. **Citations can be verified outside the model.** The model proposes a quote; code checks the quote really appears in the excerpt it was attributed to. Failed citations are dropped and counted.
3. **Risk signals can be auditable.** Red flags are thresholds over filed data, printed in the interface and linked to the filing behind them. An LLM-generated risk score can't be audited; a threshold over a shareholding filing can.
4. **All of it can be measured.** Hand-labelled questions with gold pages, a retrieval ablation, citation precision with and without the verifier, and correct refusals on unanswerable questions.

---

## 2. What you can do with it

| Page | What you see |
|---|---|
| **Landing** | A splash screen with one way in. |
| **Home** | The corpus at a glance and a live example: a verified answer next to its source page, quote highlighted. |
| **Brief** | A cited summary of any company: the latest quarter's numbers, what management said, red flags, and notable events. Every line is a verified citation, or the section says plainly that the filings don't cover it. |
| **Dashboard** | A risk screen of all 44 stocks: score, level, the flags behind them, how often each rule fires, and promoter holding and encumbrance over time. Each flag opens a **detail page** with the rule, its threshold, the filed numbers, the evidence pages and links to the original filings. |
| **Ask** | Pick a stock and ask, or click an example. The answer shows how it was checked (passages found, quotes given, confirmed or rejected); each citation shows the quote highlighted on the rendered page and a **Verify it yourself** box: open the PDF at the cited page, see the original filing on BSE, copy the exact quote. General questions also get a one-click "Open the Brief?" offer. |
| **Evaluation** | The measured results in plain language, with the technical tables one click away. |

---

## 3. Quick start

### Prerequisites
- Python 3.12 (3.10+ should work)
- Git, and about 5 GB free for the demo: ~1 GB for the download and its extracted data, the rest for the Python packages (PyTorch is the large one). The full data needs about 8 GB more.
- Optional: a free [Google AI Studio](https://aistudio.google.com) key, only needed to ask your own questions

### 1. Clone and install

```bash
git clone https://github.com/Palak8825/Filinglens.git
cd Filinglens
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Download the prebuilt data

From the [v1.0-data release](https://github.com/Palak8825/Filinglens/releases/tag/v1.0-data), download one option into a folder. The setup script checks every zip against its checksum file before extracting into `./data`.

| Option | Files | What it contains | Setup |
|---|---|---|---|
| **Demo** (~500 MB, recommended) | `filinglens-demo.zip`, `SHA256SUMS-demo.txt` | Q&A index for 5 stocks (IDEA, PAYTM, RELIANCE, VEDL, YESBANK), every page the example answers and prepared Briefs cite, red flags for all 44 stocks | `python -m scripts.setup_release <folder> --demo` |
| **Full** (~3.5 GB) | `filinglens-index.zip`, `filinglens-pdfs-part1.zip`, `filinglens-pdfs-part2.zip`, `SHA256SUMS.txt` | 14 stocks, 929 filings, 41,219 chunks, and every PDF the app can cite | `python -m scripts.setup_release <folder>` |

If a download stops part-way (on Windows, `curl` sometimes fails with an SSL error), download it in your browser instead, or resume with `curl -L --ssl-no-revoke -C - -O <file URL>`. The checksum check catches any incomplete file.

### 3. Run

```bash
uvicorn api.main:app                 # API on http://localhost:8000
streamlit run web/app.py             # UI on http://localhost:8501 (second terminal)
```

If you run the API on another port, set `FILINGLENS_API` (e.g. `FILINGLENS_API=http://localhost:8100`) before starting Streamlit.

**Without an API key**, the Brief for the demo stocks, the example answers (marked **⚡**), the dashboard, red flags and every cited page work. A new question returns a message saying a key is needed.
**With a key** (`cp .env.example .env`, set `LLM_API_KEY`), you can ask anything about the indexed stocks.

### 4. Check it works offline

```bash
python -m scripts.selftest           # under a minute, no network, no key; uses a separate data/selftest DB
```

It checks chunking (including the transcript fix below), retrieval, period resolution and the period guard, the citation verifier, red flags, the answer cache, notable-event normalisation, the Brief's "not covered" rule and eval formatting.

### Prepared content (disclosure)

The example answers (`demo/answer_cache.json`, 11 questions) and the Briefs for the 5 demo stocks (`demo/brief_cache.json`) were generated in advance by `python -m scripts.warm_cache` and `python -m scripts.warm_brief`, through **the same pipeline the app runs live**. Each entry is stored whole: answer, verified citations, dropped-citation count, the model that answered, and the date. In the UI they carry an **⚡ Example answer** or **⚡ Prepared brief** label. Questions that aren't prepared are always answered live, and live questions are never written to the cache. The cache exists so a reviewer without a key sees real pipeline output, and so a demo survives a free-tier outage.

---

## 4. How it works

```text
PDF ─ pymupdf ─ section/speaker-aware chunks (one page each) ─┬─ BM25
                                                              └─ bge-small embeddings → flat float32 file
question ─ period guard / period resolver ─ hybrid retrieve top-40 (BM25 + dense, RRF) ─ cross-encoder rerank top-8
         ─ LLM → JSON {answer, citations[{ref, quote}]} ─ citation verifier ─ answer + highlighted page images
NSE shareholding XBRL + BSE announcements / audit reports + prices ─ facts ─ core/flags.py (rules) ─ risk score
exchange announcements (all 44 stocks) ─ core/notable.py (rarity ranking, no LLM) ─ notable events ─┐
fixed questions through the same answer pipeline + red flags + notable events ───────────────────────┴─ Brief
```

- **Chunking** (`ingest/chunk.py`): about 450 tokens, never crossing a page, so every chunk has exactly one page number. Transcripts are split by speaker, and adjacent utterances are merged so an analyst's question stays with its answer.
- **Period resolver** (`core/retrieve.py`, no LLM): explicit quarters ("Q2 FY27", "Q3 FY26 vs Q2 FY27"), quarter-end dates ("as of 30 September 2026" → Q2 FY27) and relative phrases ("latest quarter", "QoQ") become a hard filter on fiscal period. The UI shows how a relative phrase was read.
- **Period guard:** when every quarter a question names is outside the indexed filings, and it isn't a range or comparison ("since FY22" still retrieves), FilingLens answers with the corpus boundary instead of searching (see the worked example in Limitations).
- **Verifier** (`core/verify.py`): a quote survives only if it appears in the cited chunk, as an exact substring or with a rapidfuzz `partial_ratio` ≥ 92 for quotes of 20+ characters; shorter quotes must match exactly and contain both a word and a number. If the model cited something but nothing verified, the answer is marked "Unverified".
- **Notable events** (`core/notable.py`, no LLM): SEBI's LODR Regulation 30 requires listed companies to disclose material events to the exchange, so the announcement feed *is* the material-events feed. FilingLens ranks that legally required stream instead of guessing what matters: rarity of each (normalised) announcement type across all 44 stocks first, then labelled boosts for legal, credit, governance and corporate-action events, a demotion for known routine paperwork, and unusual filing activity measured against the whole universe in the same window.
- **Brief** (`core/brief.py`): four fixed sections. Latest quarter (bank-style or company-style questions) and what management said run predetermined questions through the unchanged answer pipeline; red flags and notable events are deterministic. A section without a verified citation shows "The filings don't cover this."
- **Storage:** SQLite plus a flat numpy vector file. At 41k chunks × 384 dimensions, cosine similarity is one matrix multiply on CPU.

---

## 5. AI models and APIs used

| Role | Model / API | Config (`core/config.py`, `.env.example`) | Notes |
|---|---|---|---|
| Answer generation | Google Gemini API, `gemini-3.1-flash-lite` | `LLM_MODEL` | JSON mode, temperature 0. Chosen on the 30-question eval (see Evaluation). The model that actually answered is recorded with each answer. |
| Fallbacks | `gemini-3.5-flash-lite`, then `gemini-3.5-flash` | `LLM_FALLBACK_MODELS` | Used only on 429/500/503/504. `gemini-2.5-*` is retired for new keys. Fallback is off during eval runs. |
| Eval judge | Gemini API, `gemini-3-flash-preview` | `JUDGE_MODEL` | Differs from every answering model, so no model grades itself. |
| Escalation (off) | `gemini-3.5-flash` | `ESCALATE_MODEL` | Built and measurable as the `hybrid+rerank+esc` arm, but off by default: the refusals it targeted no longer occur with the current model. |
| Embeddings | `BAAI/bge-small-en-v1.5` (sentence-transformers, local CPU) | `EMBED_MODEL`, `EMBED_DIM=384` | An opt-in BGE query instruction (`EMBED_QUERY_INSTRUCTION`) was tested and changed nothing for the production setup. |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local CPU) | `RERANK_MODEL`, `RERANK=1` | |
| Data | BSE corporate announcements, NSE shareholding-pattern XBRL, Yahoo Finance prices (`yfinance`) | | Fetched once and frozen; nothing is fetched at query time. |

An Anthropic provider exists as an alternative (`LLM_PROVIDER=anthropic`); it was not used for any reported numbers. Libraries: pymupdf, rank_bm25, numpy, SQLAlchemy/SQLite, rapidfuzz, FastAPI, Streamlit/Altair, pandas.

---

## 6. Red flags

Rules only. Every flag carries its evidence (filing, page, quote or filed numbers). Thresholds come from `THRESHOLDS` in `core/flags.py` and are printed in the UI.

| Flag | Fires when | Severity | Source |
|---|---|---|---|
| `pledge_creep` | Encumbered share of promoter holding up ≥ 10pp over 4 quarters | 3 | NSE shareholding pattern (XBRL) |
| `pledge_high` | Encumbered share of promoter holding ≥ 50% | 2 | same |
| `promoter_stake_drop` | Promoter holding down ≥ 3pp in one quarter, or ≥ 5pp over 12 quarters | 2 | same |
| `auditor_change` | Statutory auditor resigns (3) or a subsidiary's auditor resigns (2) | 2–3 | BSE announcements |
| `audit_qualification` | A results filing contains a qualified, adverse or disclaimer opinion | 3 | Audit reports inside BSE results PDFs |
| `float_pump` | Largest 3-month return in the last year ≥ 100% with public float < 25% | 3 | Yahoo Finance prices + NSE public % |

Score = sum of severities: **high** at 5+, **elevated** at 2+, **low** otherwise. "Encumbered" follows SEBI's definition (pledge + non-disposal undertakings + other); the flag text shows the split. VEDL, for example, is 100% encumbered with 0% pledged, which a pledge-only check would miss. On the current data, 17 of 44 stocks are flagged; the other 27, including HDFCBANK, INFY and TCS, fire nothing.

---

## 7. Evaluation

**30 hand-labelled questions over 10 stocks** (`eval/questions.jsonl`): 9 factual, 4 reasoning, 5 cross-quarter, 2 red-flag and 10 unanswerable. Each answerable question lists every acceptable (document, page); gold figures were checked against the rendered PDF page, and the 21 newer questions were audited by two independent reviews.

The evaluation asks four questions:

**1. Does retrieval put the right page in front of the model?** (a gold page among the 8 excerpts the LLM reads)

| Retrieval | Right page in top 8 | MRR | Cross-quarter: every quarter's filing found |
|---|---|---|---|
| BM25 only | 18/20 | 0.75 | 3/5 |
| Dense only | 17/20 | 0.64 | 2/5 |
| Hybrid (RRF) | 20/20 | 0.82 | 5/5 |
| **Hybrid + rerank (FilingLens)** | **20/20** | **0.85** | **5/5** |

**2. Are the answers right?** **3. Are the citations real and on the right page?** **4. Does it refuse when the filings don't have the answer?**

| Metric (production setup) | Result |
|---|---|
| Answers correct (20 answerable) | **20/20** (factual 9/9, reasoning 4/4, cross-quarter 5/5, red-flag 2/2) |
| Verified citations on a gold page | **30/30** |
| Model citations rejected by the verifier | 0/30 |
| Correct "not in the filings" (10 unanswerable) | **10/10**, of which **1** by the period guard (a rule, reported separately so the model isn't credited for it) |
| Answerable questions wrongly refused | 0/20 |
| Median answer time | 6.0 s |

**How the answering model was chosen.** The same pipeline was run with two models:

| Setup | Correct | Cross-quarter | Wrongly refused | Median time |
|---|---|---|---|---|
| `gemini-3.1-flash-lite` (production) | 20/20 | 5/5 | 0/20 | 6.0 s |
| `gemini-3.5-flash-lite` (earlier default) | 16/20 | 2/5 | 2/20 | 10.2 s |
| `gemini-3.5-flash-lite`, no rerank | 16/20 | – | 2/20 | – |

The earlier model refused cross-quarter comparisons even with both quarters' figures in its excerpts; the current one answers them.

**Read these numbers with care.**
- 20 answerable questions is a small sample: strong evidence, not proof.
- Gold lists of three original seed questions were extended with pages the model cited that are verifiably correct (a deck table and a call page), which moved citation precision from 26/30 to 30/30; each changed question carries a note.
- Answers are graded by `gemini-3-flash-preview` against a gold answer, plus a deterministic `must_include` check where given. Cells over fewer than 5 questions print counts, not percentages.

Reproduce: `python -m eval.run_eval --configs hybrid+rerank,hybrid+rerank@3.5lite,hybrid@3.5lite`. Answers are cached in `eval/cache/`; the run writes `eval/report.md` and `eval/report.json`. A judge outage defers grading instead of losing answers (`--defer-judge`), and `--redo <ids>` re-answers specific questions after a pipeline change.

---

## 8. Key safeguards

- **Temperature 0** on every LLM call; answers only from the retrieved excerpts.
- **Quotes are checked in code**, not trusted from the model; failed citations are dropped and counted, and an answer with no verified citation is marked "Unverified".
- **Period guard:** questions about quarters outside the corpus get the corpus boundary, not a guess.
- **Red flags involve no LLM**: thresholds over filed data, printed in the UI, linked to evidence.
- **Brief sections without a verified citation say so** instead of showing uncited text.
- **Notable events involve no LLM**: a ranking of legally required disclosures, with the reason for each item shown.
- **No secrets in the repo**: `.env` is git-ignored; public filings only.

---

## 9. API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/ask` | Answer a question (cached example or live), with verified citations |
| `GET` | `/brief/{symbol}` | The Brief for a stock (prepared, or `?live=true` with a key) |
| `GET` | `/notable/{symbol}` | Ranked notable events for a stock |
| `GET` | `/flags/{symbol}` · `/screen` | Red flags for one stock · risk screen of all stocks |
| `GET` | `/shareholding/{symbol}` | Quarterly promoter holding and encumbrance |
| `GET` | `/doc/{id}/page/{page}.png` | Rendered page image (`?hl=` highlights a quote) |
| `GET` | `/doc/{id}/pdf` · `/doc/{id}/info` | The filing itself (opens at `#page=N`) · its metadata and BSE link |
| `GET` | `/evidence/pdf` · `/evidence/page.png` | Flag-evidence filings outside the search index (paths confined to `data/raw`) |
| `GET` | `/overview` · `/eval` · `/examples` · `/briefs` · `/status` | Corpus stats · eval report · example questions · prepared briefs · key/model status |

---

## 10. Project structure

```text
filinglens/
├── api/main.py              # FastAPI: ask, brief, notable, flags, pages, PDFs, eval
├── core/
│   ├── answer.py            # retrieve → rerank → LLM → verify; period guard; escalation (off)
│   ├── retrieve.py          # hybrid BM25 + dense retrieval, RRF, period resolver and guard
│   ├── rerank.py            # cross-encoder reranker
│   ├── verify.py            # citation verifier
│   ├── flags.py             # rule-based red flags and thresholds
│   ├── notable.py           # Reg-30 notable-event ranking (no LLM)
│   ├── brief.py             # the per-stock Brief
│   ├── answer_cache.py      # prepared example answers
│   ├── llm.py, config.py    # Gemini/Anthropic wrapper with fallbacks; settings
│   └── db.py, vectors.py, periods.py
├── ingest/                  # BSE fetch, PDF parse, chunking, embeddings, shareholding/auditor/market facts
├── eval/                    # questions.jsonl, run_eval.py, harvest.py, report.md/json
├── scripts/                 # setup_release, package_release, selftest, warm_cache, warm_brief, ocr_noise
├── demo/                    # questions.json, answer_cache.json, brief_cache.json, notable_subjects.json
├── web/app.py               # Streamlit UI
└── .streamlit/config.toml   # theme (light and dark)
```

---

## 11. Limitations

- **Verified isn't correct: a worked example.** The verifier is a guarantee, not a catch: every quote shown has been checked against its source page, and across 30 citations in the final eval run it rejected none. Its boundary is that it checks a quote is *present*, not what it *means*. Our own eval caught this:
  - *Question:* "What was HDFC Bank's net interest margin in Q1 FY22?" (unanswerable: the corpus starts in 2023)
  - *Answer given:* "In Q1 FY22 … core net interest margin was at 4.1%", with one **verified** citation
  - *Where the quote really came from:* the **Q1 FY24** earnings call ("The core net interest margin for the quarter was at 4.1%"). The quote was real; the period label was wrong, and the verifier can't see periods.
  - *Fix:* the period guard (`core/retrieve.py: uncovered_period`), counted separately in the eval.
- **Reasoning isn't verified.** The verifier checks quotes, not the reasoning built on them.
- **Filer-OCR noise.** Some filings carry a text layer made by the company's own scanner software. A heuristic (`python -m scripts.ocr_noise`) finds likely OCR misreadings (e.g. "financlal") on about 3.3% of indexed pages (611 of 18,707) and 4.2% of results pages; the verifier can't catch these, because the text is present, just wrong.
- **Image-only pages** (about 4.7% of PDF pages) have no text layer and aren't indexed.
- **Notable events see a narrow feed.** The downloader kept announcements about auditors, resignations and appointments, so notable events rank leadership and auditor changes; court rulings and similar events surface through results and earnings calls instead (e.g. the YES Bank AT1 Supreme Court ruling appears in the Brief's and Ask's cited answers, not in notable events).
- **Brief coverage depends on what's indexed.** For VEDL, the latest quarter's press release isn't indexed, so the Brief says the filings don't cover that section rather than filling it in.
- **Frozen corpus:** 44 stocks and 2,924 filings since April 2023; not live. Q&A covers 14 stocks and skips annual reports (CPU embedding cost); flags cover all 44; the demo download indexes 5.
- **Heuristic flags, not advice.** `promoter_stake_drop` can't tell selling from dilution: IDEA's drop (50.36% → 25.64%) came from equity issued to the Government of India, which one of the example answers explains with citations.

---

## 12. Engineering notes: silent bugs found and fixed

None of these raised an error; each would have surfaced as quietly worse results.

- **Transcript text was being dropped.** The speaker splitter discarded text before the first speaker label on a page, usually the previous speaker still talking: about 12% of transcript text, and 38% of speaker pages, in a 40-transcript sample. Fixed in `ingest/chunk.py` with a selftest guard; transcripts re-chunked in place (`python -m ingest.run --rechunk concall`), 8,294 → 9,498 chunks.
- **A real quote with the wrong period passed the verifier.** See Limitations; fixed with the period guard.
- **Cross-quarter success was unreachable.** The corpus holds byte-identical duplicate filings and retrieval de-duplicates them, so "every gold file retrieved" could never be true. Now judged per fiscal period.
- **Judge outages discarded generated answers.** Grading is now deferred and resumed.
- **BSE links died.** BSE moves attachments from `AttachLive` to `AttachHis`; links are rewritten on the way out.
- **A retired fallback model broke the fallback chain** (`gemini-2.5-flash-lite`), and a **Streamlit deprecation** could have broken a fresh install's first page; both fixed.

---

## 13. Related work

Transcript summarisers exist for US, HK and A-share markets (e.g. BeeBee AI). FilingLens differs in its India-specific filing types, page-level citations verified in code, deterministic risk flags, a Reg-30-based notable-events ranking, and a published evaluation.
