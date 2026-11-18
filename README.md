# FilingLens

### Answers from Indian company filings, proven on the page

**Ask a question about a listed company. Get a plain answer, plus the exact page of the company's own filing it came from, with the sentence highlighted.**

Built solo for **Arthaton 2026** (AI for personal and business finance).
🎬 **Demo video:** _<YouTube link>_ · 📦 **Data download:** [v1.0-data release](https://github.com/Palak8825/Filinglens/releases/tag/v1.0-data)

![FilingLens home page: a verified answer next to the highlighted source page](docs/images/home.png)

**Contents:** [Why FilingLens?](#why-filinglens) · [See it in action](#see-it-in-action) · [How it works](#how-it-works) · [Quick start](#quick-start) · [AI models, APIs and libraries](#ai-models-apis-and-libraries) · [Does it work? (Evaluation)](#does-it-work-evaluation) · [Red flags](#red-flags) · [Limitations](#limitations) · [Under the hood](#under-the-hood) (safeguards, FilingLens's own API, **project structure**, engineering notes) · [License](#license)

---

## Why FilingLens?

Every listed company in India files its quarterly results, earnings-call transcripts and shareholding details with the stock exchanges. It's all public, and it's the most reliable information there is. But it runs to **hundreds of pages per company per quarter**, mostly dense PDFs and tables.

You could paste a filing into a chatbot, but chatbots sometimes **make things up**: a confident number, a quote nobody ever said. With money involved, "probably right" isn't good enough.

FilingLens is built around one rule: **if it can't show you the page, it doesn't say it.**

- 📄 **Every claim comes with its source page**, with the exact sentence highlighted, and a button to open the original PDF.
- ✅ **Every quote is checked by code before you see it.** If the AI cites something that isn't on that page, the citation is thrown out.
- 🚩 **Warning signs from simple, visible rules**, not AI guesswork. For example, "promoters pledged more of their shares" or "the auditor resigned", each with the filing that proves it.
- 📊 **It's measured.** 30 hand-checked test questions, published results, including where it fails.

> **Who it's for:** a retail investor doing homework before buying a stock, a student learning to read company results, or anyone who wants to check what a company *actually* said. It's a research tool: no trading, no tips, no money involved.

---

## See it in action

**Example:** *"How did YES Bank's gross NPA ratio change from Q1 FY27 to Q2 FY27?"*

> **Answer:** YES Bank's gross NPA ratio improved from 1.3% in Q1 FY27 to 1.2% in Q2 FY27.
> **Source:** Results · Q2 FY27 · Page 11, *"GNPA ratio at 1.2% compared to 1.6% in Q2FY26 and 1.3% in Q1FY27"* ✅ verified

The answer shows how it was checked (8 passages read, 1 quote given, 1 confirmed, 0 rejected), and the page itself, with the line highlighted:

![Ask page: answer, the 'how this answer was checked' strip, and the highlighted source page](docs/images/ask.png)

### What you can do

| | |
|---|---|
| **📋 Brief:** a one-page summary of any company: the latest quarter's numbers, what management said, red flags and notable filings. Every line is a checked quote; if it can't find a quote for something, it says so instead of guessing. Full Briefs for the 14 indexed companies; red flags and notable filings for all 44. | ![Brief page](docs/images/brief.png) |
| **🚩 Risk dashboard:** all 44 companies screened for warning signs, with a score, the rules that fired, and promoter holding over time. | ![Risk dashboard](docs/images/dashboard.png) |
| **🔍 Flag details:** click any flag to see the rule, its threshold, the filed numbers and a link to the original filing on NSE. | ![Flag detail page](docs/images/flag.png) |
| **📈 Evaluation:** the test results in plain language, with the technical tables one click away. | ![Evaluation page](docs/images/eval.png) |

---

## How it works

The whole idea fits in one picture: **find the right pages → let the AI answer only from them → check every quote in code.**

```mermaid
flowchart TD
  subgraph Offline["🗂️ Built once, offline"]
    A["📄 2,924 filings from BSE and NSE"] --> B["✂️ Chunking: passages that never cross a page"]
    B --> C["🔤 Keyword index (BM25)"]
    B --> D["🧠 Meaning index (embeddings)"]
  end
  Q["❓ Your question"] --> P["🗓️ Work out the quarter: 'latest' → Q2 FY27"]
  P --> R["🔎 Hybrid retrieval: 40 best passages"]
  C --> R
  D --> R
  R --> K["🏅 Reranker keeps the best 8"]
  K --> L["🤖 LLM answers, quoting its sources"]
  L --> V{"🔍 Verifier: is each quote<br/>really on that page?"}
  V -- yes --> OK["✅ Answer + highlighted page"]
  V -- no --> X["❌ Citation dropped and counted"]

  style Offline fill:#f8fafc,stroke:#cbd5e1,color:#334155

  classDef data fill:#eef4ff,stroke:#3b6fd8,color:#0b2a66
  classDef step fill:#ffffff,stroke:#3b6fd8,color:#0b2a66
  classDef ai fill:#f3ecff,stroke:#7c4dff,color:#2d1065
  classDef good fill:#e8f7ee,stroke:#1f9d55,color:#0d4d2a
  classDef bad fill:#fdecec,stroke:#d64545,color:#6b1111
  class A,B,C,D data
  class Q,P,R,K step
  class L ai
  class V,OK good
  class X bad
```

Here's what each step does, using the YES Bank question above:

1. **Chunking.** Each filing is cut into passages of about 450 tokens (a few paragraphs) that **never cross a page break**, so every passage knows its exact page number. That's what makes "page 11" possible. Earnings calls are split by speaker, so an analyst's question stays with management's answer.
2. **Understanding the quarter.** "Latest quarter", "Q2 FY27" or "as of 30 September 2026" all become *Q2 FY27*, and the search only looks at that quarter's filings. No AI is needed for this step; it's plain rules.
3. **Retrieval: finding the right pages.** This is **hybrid RAG** (retrieval-augmented generation: the AI answers only from passages that were retrieved for it). Two searches run side by side:
   - **keyword search** (BM25) catches exact terms like "GNPA" or "CASA";
   - **meaning search** (embeddings) catches paraphrases, like "bad loans" for "NPA".

   Their results are merged (Reciprocal Rank Fusion), giving the 40 most promising passages.
4. **Reranking.** A second, sharper model (a cross-encoder) reads each of the 40 passages next to the question and keeps the **best 8**. Only those 8 go to the AI.
5. **Answering.** The LLM (Google Gemini) must answer from those 8 passages only, and back every claim with a **word-for-word quote** and the passage it came from.
6. **Verifying.** Code, not the AI, checks that every quote really appears in the passage it points to. Quotes that don't match are dropped and counted. An answer left with no verified quote is marked "Unverified".
7. **Showing the proof.** Each verified quote is highlighted on a picture of the actual PDF page, with buttons to open the PDF and the original filing on the BSE website.

The **Brief** reuses the same pipeline: it asks a fixed set of questions about each company (latest results, management commentary), then adds the red flags and the **notable filings**. Those are ranked by how rare each type of announcement is across all 44 companies. Indian law (SEBI's Regulation 30) already requires companies to report material events, so FilingLens ranks that legal feed instead of asking an AI what seems important.

There's no vector database and no LangChain: just SQLite, a numpy file and a few hundred lines of retrieval code you can read end to end.

---

## Quick start

You'll need **Python 3.12** (3.10+ should work), Git, and **about 5 GB of free disk space**: ~1 GB for the demo data and the rest for Python packages (PyTorch is the big one). **No API key is needed** to try it.

**1. Get the code**

```bash
git clone https://github.com/Palak8825/Filinglens.git
cd Filinglens
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**2. Download the data** from the [v1.0-data release](https://github.com/Palak8825/Filinglens/releases/tag/v1.0-data) into any folder, then unpack it. The script checks every file's checksum first.

| Option | Download | What you get | Command |
|---|---|---|---|
| **Demo** (~870 MB, recommended) | `filinglens-demo.zip` + `SHA256SUMS-demo.txt` | All 14 indexed companies: every prepared Brief and example answer with the pages they cite, the latest two quarters' results and earnings calls for your own questions, and red flags for all 44 | `python -m scripts.setup_release <folder> --demo` |
| **Full** (~3.5 GB) | `filinglens-index.zip`, `filinglens-pdfs-part1.zip`, `filinglens-pdfs-part2.zip`, `filinglens-pdfs-part3.zip` + `SHA256SUMS.txt` | 14 companies, 931 filings, 41,314 passages | `python -m scripts.setup_release <folder>` |

If a download stops part-way (on Windows, `curl` sometimes fails with an SSL error), use your browser, or resume with `curl -L --ssl-no-revoke -C - -O <file URL>`.

**3. Run it** (two terminals)

```bash
uvicorn api.main:app             # the engine, on http://localhost:8000
streamlit run web/app.py         # the app, on http://localhost:8501
```

If the engine runs on another port, set `FILINGLENS_API=http://localhost:<port>` before starting the app.

- **Without a key:** the Briefs for all 14 indexed companies, the example answers, the dashboard, the red flags and every cited page all work.
- **With a key** (copy `.env.example` to `.env` and set `LLM_API_KEY`; the free [Google AI Studio](https://aistudio.google.com) tier works), you can ask anything about the indexed companies.

**4. Check everything works** (optional): `python -m scripts.selftest` runs offline in under a minute, with no key and no internet, and tests chunking, retrieval, the verifier and the red flags.

<details>
<summary><b>⚡ Why some answers are marked "Example answer"</b></summary>

So that anyone can try FilingLens without an API key, the 11 example questions and the Briefs for all 14 indexed companies were generated in advance (`scripts/warm_cache.py`, `scripts/warm_brief.py`) using **exactly the same pipeline the app runs live**. One addition for Briefs: if a section came back without a quotable answer, or had a sentence dropped for stating a figure that isn't in its quotes, it was asked once more with a reminder to quote every figure, and the better of the two was kept. Every prepared figure was also checked by hand against its page. Each one is stored complete, with its verified citations, the model that answered and the date, and carries an **⚡ Example answer** / **⚡ Prepared brief** label. Any question that isn't one of these is always answered live, and live answers are never saved into the examples. This also keeps the demo working if the free AI tier is having a bad day.
</details>

---

## AI models, APIs and libraries

**Only one step uses a hosted AI service: writing the final answer.** The search models (embeddings and reranker) run on your own computer, with no network call. That is why everything except a brand-new question works without a key, and why the free Gemini tier is enough.

| Role | Model / service | Notes |
|---|---|---|
| Answers | Google Gemini `gemini-3.1-flash-lite` (Gemini API) | Temperature 0, JSON output; chosen on the evaluation |
| Fallbacks | `gemini-3.5-flash-lite`, then `gemini-3.5-flash` | Only when the main model is overloaded (429/5xx) |
| Evaluation judge | `gemini-3-flash-preview` | Never the same model as the one answering |
| Embeddings (meaning search) | `BAAI/bge-small-en-v1.5`, runs locally on CPU | 384 dimensions, no API call |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2`, runs locally on CPU | No API call |
| Data | BSE announcements, NSE shareholding XBRL, Yahoo Finance prices (via `yfinance`) | Downloaded once and frozen |

**Getting a key:** a free Gemini API key from [Google AI Studio](https://aistudio.google.com) goes in `.env` as `LLM_API_KEY`. All settings live in `core/config.py` and `.env.example`. An Anthropic provider is included as an option but wasn't used for any reported numbers.

**Open-source libraries, with thanks:** [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (PDF text and page images) · [rank_bm25](https://github.com/dorianbrown/rank_bm25) (keyword search) · [sentence-transformers](https://github.com/UKPLab/sentence-transformers) (embeddings and reranker) · [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) (quote checking) · [NumPy](https://numpy.org) · [pandas](https://pandas.pydata.org) · [SQLAlchemy](https://www.sqlalchemy.org) · [FastAPI](https://fastapi.tiangolo.com) + [Uvicorn](https://www.uvicorn.org) · [Streamlit](https://streamlit.io) · [Altair](https://altair-viz.github.io) (charts) · [pydantic](https://docs.pydantic.dev) + pydantic-settings · [tenacity](https://github.com/jd/tenacity) (retries) · [requests](https://requests.readthedocs.io) · [yfinance](https://github.com/ranaroussi/yfinance) · [pytesseract](https://github.com/madmaze/pytesseract) (optional OCR) · [tqdm](https://github.com/tqdm/tqdm) · [python-dotenv](https://github.com/theskumar/python-dotenv) · [google-generativeai](https://github.com/google-gemini/generative-ai-python) · [anthropic](https://github.com/anthropics/anthropic-sdk-python). Full list with versions in `requirements.txt`.

---

## Does it work? (Evaluation)

I wrote **30 test questions** about 10 companies and, for each one, recorded the correct answer and the exact page or pages where it appears in the filings. These are the **gold pages**. Each gold figure was checked by hand against the PDF. Then FilingLens answered every question and was scored against them.

**A sample of the test questions:**

| Type | Company | Question | Correct answer |
|---|---|---|---|
| Factual | YES Bank | What was the net interest margin in Q2 FY27? | 2.6% |
| Cross-quarter | Infosys | How did FY27 revenue growth guidance change between Q1 FY27 and Q2 FY27? | Cut from 1.5–3% to 1–2% |
| Reasoning | Vodafone Idea | Why did the Government of India become a shareholder in 2023? | Interest owed under the 2021 telecom relief package was converted into equity |
| Red flag | Paytm | Which statutory auditor of Paytm Payments Services resigned, and when? | Price Waterhouse Chartered Accountants LLP, effective 7 Aug 2023 |
| Not in the filings | HDFC Bank | What was the net interest margin in Q1 FY22? | Should say "not in the filings" (the collection starts in 2023) |
| Not in the filings | ZEE | How many paying subscribers does ZEE5 have? | Should say "not in the filings" |

The full set: 9 factual, 4 reasoning, 5 cross-quarter, 2 red-flag and **10 deliberately unanswerable** questions, to test whether it admits when it doesn't know (`eval/questions.jsonl`).

**The results:**

| Question we asked | Result |
|---|---|
| Are the answers correct? | **20 / 20** answerable questions |
| Is every citation on a correct (gold) page? | **30 / 30** citations |
| Does it say "not in the filings" when it should? | **10 / 10**. One of those came from a simple rule rather than the AI, and is counted separately |
| Does it wrongly refuse questions it could answer? | **0 / 20** |
| How fast? | about **6 seconds** per answer |

**Why hybrid retrieval?** An answer can only be right if the right page reaches the AI, so I tested four ways of searching. The score is how often a gold page landed in the 8 passages the AI reads:

| Search method | Right page in top 8 | Cross-quarter: found every quarter needed |
|---|---|---|
| Keyword only (BM25) | 18 / 20 | 3 / 5 |
| Meaning only (embeddings) | 17 / 20 | 2 / 5 |
| Both combined (hybrid) | 20 / 20 | 5 / 5 |
| **Both + reranker (FilingLens)** | **20 / 20** | **5 / 5**, and puts the right page nearer the top |

**Choosing the AI model.** I ran the same pipeline with two Gemini models. `gemini-3.1-flash-lite` got **20/20** in 6.0 s; `gemini-3.5-flash-lite` got 16/20 in 10.2 s, because it refused cross-quarter comparisons even with both quarters' numbers in front of it. So the app uses 3.1-flash-lite. Note that the model was picked on these same 30 questions, so the headline numbers are an in-sample result.

**Read these numbers with care.**
- 20 answerable questions is a small sample: strong evidence, not proof.
- Gold pages for three of the original questions were extended with pages the model cited that are verifiably correct (for example, the same figure in a results table), which moved citation precision from 26/30 to 30/30. Each change is noted in the file. Because those were the production setup's citations, the hybrid + reranker row in the search table above benefits slightly.
- Answers are graded by a different AI model (`gemini-3-flash-preview`) than the one answering, so no model grades its own work, plus a plain must-include check where possible.

Reproduce: `python -m eval.run_eval --configs hybrid+rerank,hybrid+rerank@3.5lite,hybrid@3.5lite` (writes `eval/report.md`).

---

## Red flags

No AI here: just clear rules over what companies file. Each flag shows its rule, its threshold and the filing behind it (`core/flags.py`).

| Flag | In plain words | Fires when | Severity |
|---|---|---|---|
| `pledge_creep` | Promoters are tying up more of their shares | Encumbered share of promoter holding up ≥ 10 points over 4 quarters | 3 |
| `pledge_high` | Most promoter shares are tied up | Encumbered share of promoter holding ≥ 50% | 2 |
| `promoter_stake_drop` | Promoters own a lot less than before | Holding down ≥ 3 points in a quarter, or ≥ 5 points over 12 quarters | 2 |
| `auditor_change` | The auditor walked away | Statutory auditor resigns (3), or a subsidiary's auditor resigns (2) | 2–3 |
| `audit_qualification` | The auditor wouldn't fully sign off | A qualified, adverse or disclaimer opinion in a results filing | 3 |
| `float_pump` | A huge price jump in a thinly traded stock | ≥ 100% rise in 3 months with public float < 25% | 3 |

The score is the sum of severities: **high** at 5+, **elevated** at 2+. "Encumbered" follows SEBI's definition (pledges, plus non-disposal undertakings and other restrictions). Vedanta, for example, is 100% encumbered but 0% pledged, which a pledge-only check would miss. Today, 17 of the 44 companies are flagged; the other 27, including HDFC Bank, Infosys and TCS, fire nothing.

---

## Limitations

**"Verified" means the quote is real, not that the answer is right.** My own testing caught this twice, for two different reasons:

1. **Wrong period.** Asked *"What was HDFC Bank's net interest margin in Q1 FY22?"* (a period the collection doesn't cover), an early version answered "4.1%" with a **verified** citation. The quote was real, but it came from the **Q1 FY24** earnings call. **The fix:** a rule now spots questions about quarters outside the collection and answers with the collection's boundary instead of searching.
2. **Wrong column.** Asked for Vedanta's latest-quarter revenue, Ask answered "₹6,395 crore", citing a real row of a results table. But when a PDF table is turned into text, the numbers lose their column headers: that figure sat under the *previous* quarter, and the current quarter's cell was empty. **The mitigation:** any quote that is mostly figures now carries a *"Read from a table: check the column on the page"* note, and the Brief asks the model to cite the table's header row too, so the period of each number can be checked.

In both cases the verifier did its job: it checks that a quote *exists* on the page, not which period it describes. The evaluation has the same blind spot, because citations are scored by page: a wrong-column read on the right page still counts as a hit.

Other honest caveats:
- **The reasoning isn't verified.** The quotes are checked; the conclusions drawn from them are not.
- **Scanner typos.** Some companies' PDFs contain OCR errors from their own scanners (e.g. "financlal"), on about 3% of pages. The verifier can't catch these: the text is really there, just misspelled.
- **About 5% of pages are pure images** with no text, so they can't be searched or cited.
- **Some "transcripts" on BSE are just a link.** Three companies filed a one-page cover letter pointing to their website instead of the transcript itself. For Adani Enterprises and IndusInd Bank (Q2 FY27), the transcript was downloaded from the company's own website and indexed, with that URL as its source. Vedanta's could not be fetched, so its Brief says it found no quotable answer for what management said.
- **Notable filings see a narrow feed.** The downloader mostly kept announcements about auditors and leadership changes, so court rulings (like the YES Bank AT1 Supreme Court verdict) show up through results and earnings calls instead.
- **Frozen snapshot:** 44 companies, 2,924 filings since April 2023, not live. Questions and full Briefs cover 14 companies (no annual reports, to keep it runnable on a laptop); the other 30 are screened for red flags only.
- **Damaged text in a company's own PDF.** Zee's Q2 FY27 results PDF has a text layer that reads "2,472" for a total income the printed page shows as 22,472. A quote of it verifies, because the text really says that. FilingLens found this while checking the prepared Briefs by hand, and its Brief now explains why it shows no figure rather than a wrong one. There may be similar cases that haven't been found.
- **The flags are signals, not advice.** A falling promoter stake can mean dilution rather than selling: Vodafone Idea's drop (50.4% → 25.6%) came from shares issued to the government, which one of the example answers explains with citations.
- **The flags miss accounting and governance problems.** Checked against five companies in the collection with well-known troubles during the data window: the rules flag **Gensol** (high: rising promoter encumbrance and a falling promoter stake, before its insolvency), **PC Jeweller** (high: a modified audit opinion, a statutory auditor's resignation and a falling promoter stake) and **Vodafone Idea** (high, though that is dilution, as above). They score **IndusInd Bank** and **Zee** at zero: IndusInd's 2025 derivatives accounting problem and the regulatory scrutiny of Zee's promoters left no trace in shareholding patterns or auditor filings, which is all the rules read. (YES Bank's crisis was in 2020, before the data starts.) So: 3 of 5 flagged, and the flags are a first screen, not a verdict.

---

## Under the hood

<details>
<summary><b>Key safeguards</b></summary>

- Temperature 0, and answers only from the retrieved passages.
- Quotes are checked in code; failed citations are dropped and counted. Words may differ slightly (scanner noise), but every figure must match the page exactly, in the very passage the quote matched.
- Questions about quarters outside the collection get the boundary, not a guess.
- Red flags and notable filings use no AI.
- Brief sections without a verified quote say "No quotable answer found", never an uncited answer.
- Figures quoted from a table carry a "check the column on the page" note (a fixed rule, no AI).
- In a Brief, any sentence stating a figure that appears in none of its quotes is removed before you see it (a fixed rule, no AI). Checking the prepared Briefs found the model computing changes itself, and once taking a figure from a mislabelled chart.
- No secrets in the repo (`.env` is git-ignored); public filings only.
</details>

<details>
<summary><b>FilingLens's own API (for developers)</b></summary>

| Method | Endpoint | What it returns |
|---|---|---|
| `POST` | `/ask` | An answer with verified citations |
| `GET` | `/brief/{symbol}` | A company's Brief |
| `GET` | `/notable/{symbol}` | Ranked notable filings |
| `GET` | `/flags/{symbol}` · `/screen` | Red flags for one company · all companies |
| `GET` | `/shareholding/{symbol}` | Promoter holding and encumbrance by quarter |
| `GET` | `/doc/{id}/page/{page}.png` | A rendered page (`?hl=` highlights a quote) |
| `GET` | `/doc/{id}/pdf` · `/doc/{id}/info` | The filing PDF · its details and BSE link |
| `GET` | `/overview` · `/eval` · `/examples` · `/briefs` · `/status` | Stats · evaluation · examples · prepared Briefs · key status |
</details>

<details>
<summary><b>Project structure</b></summary>

```text
filinglens/
├── api/main.py              # the engine (FastAPI): ask, brief, flags, pages, PDFs, eval
├── core/
│   ├── retrieve.py          # hybrid search (BM25 + embeddings), quarter detection
│   ├── rerank.py            # the reranker
│   ├── answer.py            # retrieve → rerank → LLM → verify
│   ├── verify.py            # the citation checker
│   ├── flags.py             # red-flag rules and thresholds
│   ├── notable.py           # notable-filing ranking (no AI)
│   ├── brief.py             # the per-company Brief
│   └── llm.py, config.py, db.py, vectors.py, ...
├── ingest/                  # download filings, read PDFs, chunk, embed
├── eval/                    # test questions, evaluation runner, report
├── scripts/                 # setup_release, selftest, warm_cache, warm_brief, ...
├── demo/                    # example questions and prepared answers
├── docs/images/             # screenshots for this README
└── web/app.py               # the app (Streamlit)
```
</details>

<details>
<summary><b>Engineering notes: silent bugs found and fixed</b></summary>

None of these raised an error; each would have quietly made results worse.

- **Earnings-call text was being dropped.** Text at the top of a page, before the first speaker label, was thrown away: about 12% of transcript text. Fixed, with a test; transcripts re-processed (8,294 → 9,498 passages).
- **The checker could be fooled by a changed number.** It matched quotes fuzzily, to forgive scanner noise, and fuzzy matching barely notices digits: a long quote with 1.2% turned into 1.3%, or with last year's revenue in place of this year's, still scored ~99% and was accepted. Spotted by an outside reviewer and confirmed on this code. Figures are now matched exactly against the passage the quote matched. The reviewer's second look found that the first fix still let a dropped digit through ("6,395" against a page reading "16,395"), because it checked that the quote's digits were *contained in* the passage's rather than *identical to* them; that is fixed too. 9 new tests cover these cases. Re-checking all 330 stored citations (example answers, Briefs and every evaluation run): none of the shipped answers or the headline evaluation changes; one citation in a comparison run ("Revenue from operations | 4,869" quoted from a page reading "4,869.05") is now correctly rejected, and `eval/report.md` was regenerated. `python -m scripts.recheck_cached_citations` repeats the check.
- **A real quote with the wrong period passed the checker.** See Limitations; fixed with the out-of-range quarter rule.
- **Cross-quarter success was impossible to score.** Duplicate filings meant "every gold file found" could never be true; it's now judged per quarter.
- **Judge outages threw away answers.** Grading is now deferred and resumed.
- **BSE links went dead** when BSE moved old attachments; links are now rewritten.
- **A retired Gemini model broke the fallback chain**; replaced.
</details>

---

## Related work

Transcript summarisers exist for US, Hong Kong and Chinese markets (e.g. BeeBee AI). FilingLens is different in four ways: it focuses on Indian filing types, every citation is page-level and checked in code, its risk flags are rule-based, and it publishes its evaluation.

---

## License

The code is released under the [MIT License](LICENSE) © 2026 Palak Agarwal. The filings, shareholding data and prices it works with belong to their original publishers (the companies, BSE, NSE and Yahoo Finance) and are not covered by this license.
