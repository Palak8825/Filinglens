"""
FilingLens UI. Four pages: Home (landing), Dashboard (risk screen + charts), Ask (Q&A with verified,
highlighted page citations), Evaluation (the committed eval report).

    streamlit run web/app.py        (the API must be running: uvicorn api.main:app)

Theme: .streamlit/config.toml (light and dark are both designed). Chart colours follow the validated
categorical palette; risk levels use the reserved status colours and always carry an icon + label.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import altair as alt
import pandas as pd
import requests
import streamlit as st

API = os.environ.get("FILINGLENS_API", "http://localhost:8000")

st.set_page_config(page_title="FilingLens", page_icon="🔎", layout="wide", initial_sidebar_state="collapsed")

# Status palette (fixed, never themed). Text colour picked for contrast on each fill.
LEVEL = {
    "low": {"fill": "#0ca30c", "ink": "#0b0b0b", "icon": "●", "label": "Low"},
    "elevated": {"fill": "#ec835a", "ink": "#0b0b0b", "icon": "▲", "label": "Elevated"},
    "high": {"fill": "#d03b3b", "ink": "#ffffff", "icon": "■", "label": "High"},
}
FLAG_NAMES = {
    "pledge_creep": "Encumbrance rising", "pledge_high": "Encumbrance high", "promoter_stake_drop": "Promoter stake drop",
    "auditor_change": "Auditor change", "audit_qualification": "Audit qualification", "float_pump": "Low-float price spike",
}

# ------------------------------------------------------------------------------------------- styles
st.markdown("""
<style>
  .block-container { padding-top: 2.2rem; max-width: 1280px; }
  [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
  .fl-hero { background: linear-gradient(120deg, #0d366b 0%, #1c5cab 55%, #2a78d6 100%); color: #fff;
             border-radius: 1rem; padding: 2.4rem 2.6rem 2.2rem; margin: 1.2rem 0 1.4rem; }
  .fl-hero h1 { color: #fff; font-size: 2.6rem; margin: 0 0 .4rem; letter-spacing: -0.02em; }
  .fl-hero p { color: #dbe8fb; font-size: 1.12rem; max-width: 50rem; margin: 0; line-height: 1.55; }
  .fl-hero .fl-tags { margin-top: 1.1rem; display: flex; gap: .5rem; flex-wrap: wrap; }
  .fl-hero .fl-tag { background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.28); color: #fff;
                     padding: .25rem .7rem; border-radius: 999px; font-size: .82rem; font-weight: 500; }
  .fl-card { border: 1px solid rgba(127,127,127,.22); border-radius: .8rem; padding: 1.1rem 1.2rem;
             background: rgba(127,127,127,.04); height: 100%; }
  .fl-card h4 { margin: .2rem 0 .45rem; font-size: 1.02rem; }
  .fl-card p { margin: 0; font-size: .92rem; opacity: .85; line-height: 1.5; }
  .fl-card .fl-ico { font-size: 1.35rem; }
  .fl-stat { border: 1px solid rgba(127,127,127,.22); border-radius: .8rem; padding: .9rem 1.1rem;
             background: rgba(127,127,127,.04); margin-bottom: .6rem; min-height: 6.4rem; }
  .fl-stat .v { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; }
  .fl-stat .l { font-size: .82rem; opacity: .75; margin-top: .15rem; }
  .fl-stat .s { font-size: .76rem; opacity: .6; margin-top: .1rem; }
  .fl-pipe { display: flex; align-items: stretch; gap: .35rem; flex-wrap: wrap; margin: .3rem 0 .4rem; }
  .fl-pipe .step { flex: 1 1 8.5rem; border: 1px solid rgba(127,127,127,.22); border-radius: .6rem;
                   padding: .6rem .7rem; background: rgba(127,127,127,.04); font-size: .82rem; line-height: 1.35; }
  .fl-pipe .step b { display: block; font-size: .86rem; margin-bottom: .1rem; }
  .fl-pipe .step.key { border-color: #2a78d6; box-shadow: inset 0 0 0 1px #2a78d6; }
  .fl-pipe .arrow { align-self: center; opacity: .45; font-size: 1rem; }
  .fl-chip { display: inline-block; padding: .18rem .62rem; border-radius: 999px; font-weight: 600;
             font-size: .8rem; letter-spacing: .01em; white-space: nowrap; }
  .fl-badge { display: inline-block; padding: .14rem .55rem; border-radius: .4rem; font-size: .78rem;
              border: 1px solid rgba(127,127,127,.3); margin-right: .3rem; white-space: nowrap; }
  .fl-badge.ok { border-color: #0ca30c; }
  .fl-badge.warn { border-color: #d03b3b; }
  .fl-check { display: flex; gap: .3rem; flex-wrap: wrap; margin: .2rem 0 .9rem; }
  .fl-check .c { flex: 1 1 9rem; border-left: 3px solid #2a78d6; padding: .35rem .6rem; font-size: .8rem;
                 background: rgba(127,127,127,.05); border-radius: 0 .4rem .4rem 0; line-height: 1.35; }
  .fl-check .c b { display: block; font-size: .92rem; }
  .fl-check .c.good { border-left-color: #0ca30c; }
  .fl-check .c.bad { border-left-color: #d03b3b; }
  .fl-quote { border-left: 3px solid #eda100; padding: .5rem .9rem; margin: .3rem 0 .7rem;
              background: rgba(237,161,0,.08); border-radius: 0 .4rem .4rem 0; font-size: .95rem; }
  .fl-answer { font-size: 1.06rem; line-height: 1.62; }
  .fl-muted { opacity: .68; font-size: .84rem; }
  .fl-section { font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
                opacity: .6; margin: 1.6rem 0 .5rem; }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------------------------------- data
@st.cache_data(ttl=300, show_spinner=False)
def get_json(path: str):
    r = requests.get(f"{API}{path}", timeout=60)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_png(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=60)
    except requests.RequestException:
        return None
    return r.content if r.ok else None


@st.cache_data(ttl=3600, show_spinner=False)
def cached_answer(symbol: str, question: str) -> dict | None:
    r = requests.post(f"{API}/ask", json={"symbol": symbol, "question": question}, timeout=120)
    return r.json() if r.ok else None


def page_url(document_id: int | None, page: int | None, path: str | None = None, hl: str | None = None) -> str | None:
    if document_id and page:
        return f"{API}/doc/{document_id}/page/{page}.png" + (f"?hl={quote(hl)}" if hl else "")
    if path and page:
        return f"{API}/evidence/page.png?path={quote(path)}&page={page}"
    return None


def show_page(url: str | None, caption: str | None = None) -> None:
    if not url:
        return
    png = fetch_png(url)
    if png:
        st.image(png, caption=caption, width="stretch")
    else:
        st.caption("Page image not in this download (the quote was still verified against the indexed text).")


def chip(level: str) -> str:
    lv = LEVEL[level]
    return (f'<span class="fl-chip" style="background:{lv["fill"]};color:{lv["ink"]}">'
            f'{lv["icon"]} {lv["label"]}</span>')


def stat(value, label: str, sub: str = "") -> str:
    return f'<div class="fl-stat"><div class="v">{value}</div><div class="l">{label}</div>' + \
           (f'<div class="s">{sub}</div>' if sub else "") + "</div>"


def section(title: str) -> None:
    st.markdown(f'<div class="fl-section">{title}</div>', unsafe_allow_html=True)


def cell(m) -> str:
    """An eval metric {k, n, v}: counts when n < 5, else pct (k/n)."""
    if not isinstance(m, dict) or not m.get("n"):
        return "—"
    return f"{m['k']}/{m['n']}" if m["n"] < 5 else f"{m['k'] / m['n']:.0%} ({m['k']}/{m['n']})"


try:
    OVERVIEW = get_json("/overview")
except requests.RequestException as e:
    st.error(f"API not reachable at {API}. Start it with `uvicorn api.main:app`. ({e})")
    st.stop()
INDEXED = set(OVERVIEW["symbols_indexed"])


def screen_df() -> pd.DataFrame:
    df = pd.DataFrame(get_json("/screen"))
    df["Q&A"] = df["symbol"].isin(INDEXED)
    return df


def no_key_banner() -> None:
    if not OVERVIEW["has_api_key"]:
        st.info(f"No LLM API key set. Flags, the dashboard, page images and the selftest all work; Q&A answers the "
                f"{OVERVIEW['cached_answers']} cached example questions (marked *cached*). Add `LLM_API_KEY` to `.env` "
                "and restart the API to ask your own.", icon="🔑")


# ------------------------------------------------------------------------------------------- charts
def bar_chart(df: pd.DataFrame, x: str, y: str, x_title: str, tooltip: list, color: alt.Color | None = None,
              height: int | None = None) -> alt.Chart:
    """Horizontal bars, thin, 4px rounded data end, recessive grid; a tooltip on every bar."""
    enc = dict(x=alt.X(f"{x}:Q", title=x_title, axis=alt.Axis(grid=True, tickMinStep=1)),
               y=alt.Y(f"{y}:N", sort="-x", title=None,
                       axis=alt.Axis(labelOverlap=False, labelLimit=220, ticks=False, domain=False)),
               tooltip=tooltip)
    if color is not None:
        enc["color"] = color
    return (alt.Chart(df).mark_bar(cornerRadiusEnd=4, size=14)
            .encode(**enc).properties(height=alt.Step(height or 24)))


LEVEL_COLOR = alt.Color("level:N", title="Risk level",
                        scale=alt.Scale(domain=["low", "elevated", "high"],
                                        range=[LEVEL[k]["fill"] for k in ("low", "elevated", "high")]),
                        legend=alt.Legend(orient="top"))


def shareholding_chart(symbol: str, height: int = 260, narrow: bool = False) -> alt.Chart | None:
    rows = get_json(f"/shareholding/{symbol}")
    if len(rows) < 2:
        return None
    df = pd.DataFrame(rows)
    long = df.melt(id_vars=["period", "as_of"], value_vars=["promoter_pct", "encumbered_pct_of_promoter"],
                   var_name="series", value_name="pct").dropna(subset=["pct"])
    long["series"] = long["series"].map({"promoter_pct": "Promoter holding (% of company)",
                                         "encumbered_pct_of_promoter": "Encumbered (% of promoter holding)"})
    base = alt.Chart(long).encode(
        x=alt.X("as_of:T", title=None, axis=alt.Axis(format="%b %y", grid=False)),
        y=alt.Y("pct:Q", title="%", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("series:N", title=None,
                        scale=alt.Scale(domain=["Promoter holding (% of company)", "Encumbered (% of promoter holding)"]),
                        legend=alt.Legend(orient="top", direction="vertical" if narrow else "horizontal", labelLimit=320)),
        tooltip=[alt.Tooltip("period:N", title="Quarter"), alt.Tooltip("series:N", title="Series"),
                 alt.Tooltip("pct:Q", title="%", format=".2f")])
    return (base.mark_line(strokeWidth=2) + base.mark_circle(size=64)).properties(height=height)


# ------------------------------------------------------------------------------------------- pages
def home() -> None:
    st.markdown("""
    <div class="fl-hero">
      <h1>FilingLens</h1>
      <p>Ask Indian listed-company filings anything. Every claim cites the exact PDF page, and code checks each
         citation before you see it. A deterministic governance red-flag score sits beside every answer.</p>
      <div class="fl-tags"><span class="fl-tag">Hybrid RAG</span><span class="fl-tag">Out-of-model hallucination guard</span>
        <span class="fl-tag">Explainable risk engine</span><span class="fl-tag">RAG evaluation pipeline</span>
        <span class="fl-tag">NSE / BSE filings</span></div>
    </div>""", unsafe_allow_html=True)
    no_key_banner()

    o = OVERVIEW
    df = screen_df()
    flagged = int((df["score"] > 0).sum())
    tiles = [(f"{o['pdfs_in_corpus']:,}" if o["pdfs_in_corpus"] else "—", "Filings in the frozen corpus",
              "44 stocks · since April 2023"),
             (f"{o['filings_indexed']:,}", "Filings indexed for Q&A", f"{len(o['symbols_indexed'])} stocks · {o['chunks']:,} chunks"),
             (f"{o['symbols_screened']}", "Stocks screened for red flags", f"{flagged} flagged by the rule engine"),
             (f"{o['cached_answers']}", "Example answers", "work without an API key")]
    for c, (v, l, s) in zip(st.columns(4), tiles):
        c.markdown(stat(v, l, s), unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    if c1.button("💬  Ask the filings", type="primary", width="stretch"):
        st.switch_page(ASK)
    if c2.button("📊  Open the risk dashboard", width="stretch"):
        st.switch_page(DASH)

    section("What it does")
    cards = [("🔀", "Hybrid retrieval", "BM25 keyword search and dense embeddings, fused with Reciprocal Rank Fusion, "
                                       "then a cross-encoder reranks to the 8 excerpts the model reads. Quarters, quarter-end "
                                       "dates and “latest quarter” become hard period filters."),
             ("🛡️", "Hallucination guard", "The model must quote its source. Code, not the model, checks every quote is "
                                           "verbatim in the excerpt it cites. Failures are dropped and counted; if none "
                                           "survive, the answer is marked Unverified."),
             ("🚩", "Explainable risk engine", "Promoter encumbrance, stake drops, auditor exits, qualified audit opinions, "
                                               "low-float price spikes. Pure rules over filed data, thresholds printed, every "
                                               "flag linked to its filing. No LLM."),
             ("📏", "Measured, not asserted", "Hand-labelled questions with gold pages, a retrieval ablation, citation "
                                              "precision with and without the verifier, abstention on unanswerable "
                                              "questions, and a judge model distinct from every answering model.")]
    for c, (ico, t, p) in zip(st.columns(4), cards):
        c.markdown(f'<div class="fl-card"><div class="fl-ico">{ico}</div><h4>{t}</h4><p>{p}</p></div>',
                   unsafe_allow_html=True)

    section("How an answer is made")
    st.markdown("""
    <div class="fl-pipe">
      <div class="step"><b>Question</b>stock + question; period resolved (“Q2 FY27”, “30 Sep 2026”, “latest”)</div><div class="arrow">→</div>
      <div class="step"><b>Hybrid retrieve</b>BM25 + bge-small embeddings, RRF fusion, top 40</div><div class="arrow">→</div>
      <div class="step"><b>Rerank</b>cross-encoder keeps the best 8 excerpts</div><div class="arrow">→</div>
      <div class="step"><b>LLM</b>Gemini answers in JSON, each claim with a verbatim quote</div><div class="arrow">→</div>
      <div class="step key"><b>Verifier</b>code checks each quote is in its excerpt; drops the rest</div><div class="arrow">→</div>
      <div class="step"><b>Answer + page</b>verified citations, highlighted on the rendered PDF page</div>
    </div>""", unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        section("A verified citation, on the page")
        exs = get_json("/examples")
        ex = next((e for e in exs if e["symbol"] == "RELIANCE" and "Digital" in e["question"]), None) or (exs or [None])[0]
        a = cached_answer(ex["symbol"], ex["question"]) if ex else None
        if a and a.get("citations"):
            c = a["citations"][0]
            st.markdown(f"**{ex['symbol']}** · {ex['question']}")
            st.markdown(f'<div class="fl-answer">{a["answer"]}</div>', unsafe_allow_html=True)
            st.caption(f"{c['doc_type']} · {c.get('fiscal_period') or ''} · page {c['page']} · ✓ quote verified, highlighted below")
            show_page(page_url(c["document_id"], c["page"], hl=c["quote"]))
    with right:
        section("Corpus indexed for Q&A")
        types = pd.DataFrame([{"type": k.replace("_", " "), "filings": v} for k, v in o["doc_types"].items()])
        st.altair_chart(bar_chart(types, "filings", "type", "Filings",
                                  [alt.Tooltip("type:N", title="Type"), alt.Tooltip("filings:Q", title="Filings")]),
                        width="stretch")
        section("Models")
        st.markdown(f"""
| Role | Model |
|---|---|
| Answers | `{o['model']}` · Gemini API |
| Embeddings | `{o['embed_model']}` · local CPU |
| Reranker | `{o['rerank_model'] or 'off'}` · local CPU |
| Risk flags | rules only, no model |
""")
        section("Latest evaluation")
        ev = get_json("/eval")
        e2e = ev.get("e2e") or {}
        head = e2e.get("hybrid+rerank") or next(iter(e2e.values()), None)
        if head:
            g = st.columns(2)
            g[0].markdown(stat(cell(head["accuracy"]), "Answer accuracy"), unsafe_allow_html=True)
            g[1].markdown(stat(cell(head["citation_precision"]), "Citation precision"), unsafe_allow_html=True)
            g = st.columns(2)
            g[0].markdown(stat(cell(head["abstention"]), "Correct refusals"), unsafe_allow_html=True)
            g[1].markdown(stat(f"{head['median_latency_s']:.1f}s", "Median answer time"), unsafe_allow_html=True)
            st.page_link(EVAL, label="Full evaluation →", icon="📏")
    st.markdown('<p class="fl-muted" style="margin-top:2rem">Research and investor-protection tool. Not investment advice. '
                'No trading, no broker keys, public filings only.</p>', unsafe_allow_html=True)


def render_flags(f: dict, compact: bool = False) -> None:
    if not f["flags"]:
        st.success("No flags triggered on the available filings.", icon="✅")
    for i, fl in enumerate(f["flags"]):
        icon = "🔴" if fl["severity"] == 3 else "🟠"
        with st.expander(f"{icon} {fl['title']}", expanded=(not compact and i == 0)):
            st.write(fl["detail"])
            structured = [e for e in fl["evidence"] if e.get("values")]
            if structured:
                st.dataframe([{"period": e.get("period") or e.get("as_of"), **e["values"]} for e in structured],
                             width="stretch", hide_index=True)
            for e in fl["evidence"]:
                label = " · ".join(x for x in (e.get("period"), e.get("as_of"), (e.get("title") or "")[:80]) if x)
                if e.get("quote"):
                    st.markdown(f'<div class="fl-quote">{e["quote"]}</div>', unsafe_allow_html=True)
                links = f" · [source filing]({e['source_url']})" if e.get("source_url") else ""
                st.caption(label + links)
                if not compact:
                    show_page(page_url(e.get("document_id"), e.get("page"), e.get("path")), caption=f"page {e.get('page')}")
    if not compact:
        with st.expander("Thresholds"):
            st.json(f["thresholds"])


def dashboard() -> None:
    st.title("Risk dashboard")
    st.markdown('<p class="fl-muted">Deterministic rules over NSE shareholding patterns, BSE auditor filings and prices. '
                'No LLM in this path. Thresholds are listed under each stock.</p>', unsafe_allow_html=True)
    df = screen_df()
    flagged = df[df["score"] > 0]
    for c, (v, l, s) in zip(st.columns(4), [
        (len(df), "Stocks screened", "NSE/BSE listed"),
        (len(flagged), "Flagged", f"{len(df) - len(flagged)} with no flags"),
        (int((df["level"] == "high").sum()), "High risk", "score ≥ 5"),
        (int(df["Q&A"].sum()), "Also searchable in Q&A", "ask about their filings")]):
        c.markdown(stat(v, l, s), unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        section("Risk score by stock (flagged only)")
        fd = flagged.assign(flag_names=flagged["flags"].map(lambda fs: ", ".join(FLAG_NAMES.get(f, f) for f in fs)))
        st.altair_chart(bar_chart(fd, "score", "symbol", "Risk score (sum of flag severities)",
                                  [alt.Tooltip("symbol:N", title="Stock"), alt.Tooltip("score:Q", title="Score"),
                                   alt.Tooltip("level:N", title="Level"), alt.Tooltip("flag_names:N", title="Flags")],
                                  color=LEVEL_COLOR), width="stretch")
    with right:
        section("How often each rule fires")
        counts = pd.Series([FLAG_NAMES.get(f, f) for fs in df["flags"] for f in fs]).value_counts()
        fc = pd.DataFrame({"flag": counts.index, "stocks": counts.values})
        st.altair_chart(bar_chart(fc, "stocks", "flag", "Stocks flagged",
                                  [alt.Tooltip("flag:N", title="Rule"), alt.Tooltip("stocks:Q", title="Stocks")]),
                        width="stretch")
        st.markdown('<p class="fl-muted">Score = sum of flag severities (auditor exits and qualified opinions weigh '
                    'most). Thresholds for each rule are printed in the stock detail below.</p>', unsafe_allow_html=True)

    section("All stocks")
    levels = st.pills("Level", ["high", "elevated", "low"], selection_mode="multi", default=["high", "elevated", "low"],
                      format_func=lambda k: f"{LEVEL[k]['icon']} {LEVEL[k]['label']}", label_visibility="collapsed")
    table = df[df["level"].isin(levels or [])].copy()
    table["Level"] = table["level"].map(lambda k: f"{LEVEL[k]['icon']} {LEVEL[k]['label']}")
    table["Flags"] = table["flags"].map(lambda fs: ", ".join(FLAG_NAMES.get(f, f) for f in fs) or "—")
    st.dataframe(table[["symbol", "Level", "score", "Flags", "Q&A"]], hide_index=True, width="stretch",
                 column_config={"symbol": st.column_config.TextColumn("Stock"),
                                "score": st.column_config.ProgressColumn("Score", min_value=0,
                                                                         max_value=int(df["score"].max() or 1), format="%d"),
                                "Q&A": st.column_config.CheckboxColumn("Q&A indexed")})

    section("Stock detail")
    default = st.session_state.get("symbol", "IDEA")
    syms = list(df["symbol"])
    sym = st.selectbox("Stock", syms, index=syms.index(default) if default in syms else 0, label_visibility="collapsed")
    st.session_state["symbol"] = sym
    f = get_json(f"/flags/{sym}")
    a, b = st.columns([3, 2], gap="large")
    with a:
        st.markdown(f"### {sym} &nbsp; {chip(f['level'])} <span class='fl-muted'>score {f['score']}</span>",
                    unsafe_allow_html=True)
        ch = shareholding_chart(sym)
        if ch is not None:
            st.altair_chart(ch, width="stretch")
            st.caption("NSE quarterly shareholding pattern (XBRL). “Encumbered” follows SEBI: pledge + non-disposal "
                       "undertakings + other encumbrances.")
        else:
            st.caption("No promoter shareholding series for this stock (e.g. no identified promoter).")
    with b:
        render_flags(f)
        if sym in INDEXED and st.button(f"Ask about {sym}'s filings →", type="primary", width="stretch"):
            st.session_state["ask_symbol"] = sym
            st.switch_page(ASK)


def ask_page() -> None:
    st.title("Ask the filings")
    no_key_banner()
    syms = sorted(INDEXED)
    pre = st.session_state.pop("ask_symbol", None) or st.session_state.get("symbol")
    pre = pre if pre in syms else "YESBANK"
    top = st.columns([2, 3])
    symbol = top[0].selectbox("Stock", syms, index=syms.index(pre) if pre in syms else 0)
    st.session_state["symbol"] = symbol
    f = get_json(f"/flags/{symbol}")
    top[1].markdown(f"<div style='margin-top:2.1rem'>Red-flag level &nbsp;{chip(f['level'])} "
                    f"<span class='fl-muted'>&nbsp;score {f['score']} · {len(f['flags'])} flag(s)</span></div>",
                    unsafe_allow_html=True)

    examples = [e["question"] for e in get_json(f"/examples?symbol={symbol}")]
    if examples:
        picked = st.pills("Example questions · cached, they work without an API key", examples,
                          selection_mode="single", key=f"pill-{symbol}")
        if picked and st.session_state.get("last_pill") != picked:
            st.session_state["last_pill"] = picked
            st.session_state["q"] = picked
            st.session_state["pending"] = True

    with st.form("ask", border=False):
        q = st.text_input("Question", key="q",
                          placeholder="e.g. What was the net interest margin in the latest quarter and why did it move?")
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        doc_type = c1.selectbox("Restrict to", ["any", "results", "concall", "announcement", "shareholding"])
        live = c2.toggle("Ask live", value=False, disabled=not OVERVIEW["has_api_key"],
                         help="Skip the cache and send the question to the model now, even if it's an example.")
        submitted = c3.form_submit_button("Ask", type="primary", width="stretch")
    if (submitted or st.session_state.pop("pending", False)) and q:
        with st.spinner("Retrieving · reranking · answering · verifying citations…"):
            resp = requests.post(f"{API}/ask", json={"symbol": symbol, "question": q, "use_cache": not live,
                                                     "doc_type": None if doc_type == "any" else doc_type}, timeout=180)
        if resp.ok:
            st.session_state["last"] = resp.json()
        elif resp.status_code == 503:
            st.info(resp.json().get("detail", "No API key set."), icon="🔑")
        else:
            st.error(f"Answer failed ({resp.status_code}): {resp.text[:300]}")

    r = st.session_state.get("last")
    if not (r and r.get("symbol") == symbol):
        return
    main, side = st.columns([3, 2], gap="large")
    with main:
        n_ok, n_drop = len(r["citations"]), r["dropped_citations"]
        with st.container(border=True):
            badges = [f'<span class="fl-badge ok">✓ {n_ok} verified citation{"s" if n_ok != 1 else ""}</span>']
            if n_drop:
                badges.append(f'<span class="fl-badge warn">✗ {n_drop} dropped by the verifier</span>')
            badges.append(f'<span class="fl-badge">{"refused: not in the filings" if r["abstained"] else "confidence: " + r["confidence"]}</span>')
            if r.get("model"):
                badges.append(f'<span class="fl-badge">{r["model"]}</span>')
            if r.get("cached"):
                badges.append('<span class="fl-badge">cached</span>')
            st.markdown("".join(badges), unsafe_allow_html=True)
            st.markdown(f'<div class="fl-answer" style="margin-top:.7rem">{r["answer"]}</div>', unsafe_allow_html=True)
            if r.get("period_note"):
                st.caption("🗓️ " + r["period_note"])
            if r.get("cached"):
                c = r["cached"]
                st.caption(f"Cached answer, generated {c['generated_on']} with {c['model']} on {c['index_chunks']:,} "
                           f"{symbol} chunks" + (" (index has changed since)" if c["index_changed"] else "")
                           + ". Toggle *Ask live* to regenerate.")

        section("How this answer was checked")
        verdict = "good" if n_ok and not n_drop else ("bad" if n_drop else "")
        pages = len({(c["document_id"], c["page"]) for c in r["citations"]})
        st.markdown(f"""
        <div class="fl-check">
          <div class="c"><b>8 excerpts</b>hybrid retrieval, then rerank</div>
          <div class="c"><b>{n_ok + n_drop} quoted</b>citations the model proposed</div>
          <div class="c {verdict}"><b>{n_ok} kept · {n_drop} dropped</b>each quote checked verbatim in its excerpt</div>
          <div class="c"><b>{pages} page{"s" if pages != 1 else ""}</b>rendered below, quote highlighted</div>
        </div>""", unsafe_allow_html=True)

        if r["citations"]:
            tabs = st.tabs([f"[{i}] {c['doc_type']} · {c.get('fiscal_period') or '—'} · p.{c['page']}"
                            for i, c in enumerate(r["citations"], 1)])
            for tab, c in zip(tabs, r["citations"]):
                with tab:
                    st.markdown(f'<div class="fl-quote">“{c["quote"]}”</div>', unsafe_allow_html=True)
                    st.caption(f"{c['title'][:140]} · page {c['page']} · ✓ verified")
                    show_page(page_url(c["document_id"], c["page"], hl=c["quote"]))
    with side:
        section(f"{symbol} red flags")
        render_flags(f, compact=True)
        ch = shareholding_chart(symbol, height=210, narrow=True)
        if ch is not None:
            section("Promoter holding")
            st.altair_chart(ch, width="stretch")
        st.page_link(DASH, label="Full risk detail on the dashboard →", icon="📊")


def evaluation() -> None:
    st.title("Evaluation")
    ev = get_json("/eval")
    e2e, abl = ev.get("e2e") or {}, ev.get("ablation") or {}
    any_run = next(iter(e2e.values()), None)
    n_run = (any_run["n_answerable"] + any_run["n_unanswerable"]) if any_run else 0
    st.markdown(f'<p class="fl-muted">Question set: <b>{ev["n_questions"]}</b> hand-labelled questions over '
                f'{len(ev["question_symbols"])} stocks, each with gold (document, page) pairs checked against the rendered '
                f'PDF. The last committed run covered <b>{n_run}</b> of them. Cells over fewer than 5 questions show '
                'counts, not percentages. The judge model differs from every answering model.</p>', unsafe_allow_html=True)

    left, right = st.columns([2, 3], gap="large")
    with left:
        section("Question mix")
        mix = pd.DataFrame([{"type": k.replace("_", " "), "questions": v} for k, v in ev["question_types"].items()])
        st.altair_chart(bar_chart(mix, "questions", "type", "Questions",
                                  [alt.Tooltip("type:N", title="Type"), alt.Tooltip("questions:Q", title="Questions")]),
                        width="stretch")
        st.caption("Stocks: " + ", ".join(ev["question_symbols"]))
    with right:
        head_name = "hybrid+rerank" if "hybrid+rerank" in e2e else next(iter(e2e), None)
        if head_name:
            section(f"Headline · {head_name} (what the app runs)")
            h = e2e[head_name]
            g = st.columns(3)
            g[0].markdown(stat(cell(h["accuracy"]), "Answer accuracy"), unsafe_allow_html=True)
            g[1].markdown(stat(cell(h["citation_precision"]), "Citation precision", "verified citations on a gold page"),
                          unsafe_allow_html=True)
            g[2].markdown(stat(cell(h["verifier_rejection"]), "Rejected by the verifier", "share of the model's citations"),
                          unsafe_allow_html=True)
            g = st.columns(3)
            g[0].markdown(stat(cell(h["abstention"]), "Correct refusals", "on unanswerable questions"), unsafe_allow_html=True)
            g[1].markdown(stat(cell(h["false_abstention"]), "False refusals", "on answerable questions"), unsafe_allow_html=True)
            g[2].markdown(stat(f"{h['median_latency_s']:.1f}s", "Median answer time"), unsafe_allow_html=True)

    if abl:
        section("Retrieval ablation · is a gold page among the 8 excerpts the model reads?")
        rows = []
        for name, v in abl.items():
            a = v.get("all", {})
            rows.append({"config": name, "MRR": a.get("mrr"), "hit@8": cell({"k": a.get("hits"), "n": a.get("n")}),
                         "both filings (cross-doc)": cell({"k": v["cross_doc"]["all_gold"], "n": v["cross_doc"]["n"]})
                         if "cross_doc" in v else "—"})
        adf = pd.DataFrame(rows)
        c1, c2 = st.columns([3, 2], gap="large")
        c1.altair_chart(
            alt.Chart(adf).mark_bar(cornerRadiusEnd=4, size=18).encode(
                x=alt.X("MRR:Q", title="Mean reciprocal rank of the first gold excerpt (higher is better)",
                        scale=alt.Scale(domain=[0, 1])),
                y=alt.Y("config:N", sort=list(adf["config"]), title=None),
                tooltip=["config", alt.Tooltip("MRR:Q", format=".2f"), "hit@8", "both filings (cross-doc)"]
            ).properties(height=40 * len(adf) + 30), width="stretch")
        c2.dataframe(adf.assign(MRR=adf["MRR"].map(lambda x: f"{x:.2f}" if x is not None else "—")),
                     hide_index=True, width="stretch")

    if len(e2e) > 1:
        section("End to end, by configuration")
        st.dataframe(pd.DataFrame([{"config": k, "accuracy": cell(m["accuracy"]),
                                    "citation precision": cell(m["citation_precision"]),
                                    "precision w/o verifier": cell(m["citation_precision_no_verifier"]),
                                    "verifier rejected": cell(m["verifier_rejection"]),
                                    "correct refusals": cell(m["abstention"]), "false refusals": cell(m["false_abstention"]),
                                    "median s": m["median_latency_s"]} for k, m in e2e.items()]),
                     hide_index=True, width="stretch")
    st.caption("Reproduce: `python -m eval.run_eval --configs hybrid+rerank,hybrid,hybrid+rerank@flash,"
               "hybrid+rerank@weak,hybrid+rerank@v2`")


HOME = st.Page(home, title="Home", icon="🏠", default=True)
DASH = st.Page(dashboard, title="Dashboard", icon="📊", url_path="dashboard")
ASK = st.Page(ask_page, title="Ask", icon="💬", url_path="ask")
EVAL = st.Page(evaluation, title="Evaluation", icon="📏", url_path="evaluation")
st.navigation([HOME, DASH, ASK, EVAL], position="top").run()
