"""
FilingLens UI.

Pages: Landing (splash) → Home (overview + a verified citation, live) → Dashboard (risk screen) → Ask (Q&A with
highlighted, verifiable citations) → Evaluation (measured results). A Flag page explains one red flag in full.

    streamlit run web/app.py        (the API must be running: uvicorn api.main:app)

Theme: .streamlit/config.toml (light and dark both designed). Chart colours follow the validated categorical
palette; risk levels use the reserved status colours and always carry an icon + label.
"""
from __future__ import annotations

import html
import os
import re
from urllib.parse import quote

import altair as alt
import pandas as pd
import requests
import streamlit as st

# 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first and every request then waits ~2 s
# before falling back to IPv4, where uvicorn listens. A Brief makes ~20 calls, so that was ~40 s per page.
API = os.environ.get("FILINGLENS_API", "http://127.0.0.1:8000").replace("://localhost", "://127.0.0.1")

st.set_page_config(page_title="FilingLens", page_icon="🔎", layout="wide", initial_sidebar_state="expanded")

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
VALUE_LABELS = {
    "period": "Period", "promoter_pct": "Promoter holding (%)", "encumbered_pct_of_promoter": "Encumbered (% of promoter holding)",
    "pledged_pct_of_promoter": "Pledged (% of promoter holding)", "ndu_pct_of_promoter": "Non-disposal undertakings (%)",
    "other_encumbrance_pct_of_promoter": "Other encumbrance (%)", "public_pct": "Public holding (%)",
    "free_float_pct": "Free float (%)", "max_ret_3m": "Best 3-month return", "ret_3m": "Latest 3-month return",
    "price_start": "Price at start", "price_end": "Price at end",
}
DOC_TYPES = {"results": "Results", "concall": "Earnings call", "announcement": "Announcement", "shareholding": "Shareholding",
             "annual_report": "Annual report"}
QTYPES = {"factual": "Factual", "reasoning": "Reasoning", "cross_doc": "Cross-quarter", "flag": "Red flag",
          "unanswerable": "Not in the filings"}
CONFIG_NAMES = {"bm25": "Keyword search", "vector": "Meaning search", "hybrid": "Both combined",
                "hybrid+rerank": "Both + re-ranking (FilingLens)"}


def rule_text(code: str, t: dict) -> str:
    """What each rule checks, in plain words, with its threshold."""
    return {
        "pledge_creep": f"Fires when the share of the promoters' holding that is encumbered (pledged, under a non-disposal "
                        f"undertaking, or otherwise) rises by {t['encumbrance_creep_pp']:.0f} percentage points or more over four quarters.",
        "pledge_high": f"Fires when {t['encumbrance_high_abs']:.0f}% or more of the promoters' holding is encumbered.",
        "promoter_stake_drop": f"Fires when the promoters' stake falls {t['promoter_sell_qoq_pp']:.0f} percentage points in one quarter, "
                               f"or {t['promoter_sell_cum_pp']:.0f} points over three years. It can't tell selling from dilution.",
        "auditor_change": "Fires when the statutory auditor of the company or a subsidiary resigns or leaves mid-term.",
        "audit_qualification": "Fires when an auditor's report in a results filing gives a qualified, adverse or disclaimer opinion.",
        "float_pump": f"Fires when the share price rose {t['float_pump_return']:.0%} or more within three months in the last year "
                      f"while less than {t['float_pump_free_float']:.0f}% of shares were freely traded.",
    }.get(code, "")


def clean_quote(q: str | None) -> str:
    """Stored evidence quotes can start mid-word (a fixed character window was cut): drop the broken first
    token and mark the cut with an ellipsis, so a quote never reads "t to Regulation 30"."""
    q = " ".join((q or "").split())
    if not q:
        return ""
    if q[0].islower():
        q = "…" + q.split(" ", 1)[-1] if " " in q else q
    if q[-1] not in ".!?”\")":
        q += "…"
    return q


# ------------------------------------------------------------------------------------------- styles
st.markdown("""
<style>
  .block-container { padding-top: 3.2rem; max-width: 1280px; }
  [data-testid="stSidebar"] { min-width: 16rem; max-width: 16rem; }
  .fl-brand { display: flex; align-items: center; gap: .6rem; margin: .2rem 0 .3rem; }
  .fl-brand .mark { width: 2.1rem; height: 2.1rem; border-radius: .55rem; display: grid; place-items: center;
                    background: linear-gradient(135deg, #0d366b, #2a78d6); color: #fff; font-weight: 700; font-size: 1.05rem; }
  .fl-brand .name { font-weight: 700; font-size: 1.15rem; letter-spacing: -0.01em; }
  .fl-brand-sub { font-size: .8rem; opacity: .65; margin: 0 0 1rem; line-height: 1.4; }
  .fl-navcap { font-size: .74rem; opacity: .6; margin: -.45rem 0 .55rem 2.35rem; line-height: 1.3; }

  .fl-hero { position: relative; overflow: hidden; background: linear-gradient(120deg, #0b2a55 0%, #1c5cab 60%, #2a78d6 100%);
             color: #fff; border-radius: 1.1rem; padding: 2.2rem 2.4rem; margin: .8rem 0 1.2rem; }
  .fl-hero h1 { color: #fff; font-size: 2.3rem; margin: 0 0 .35rem; letter-spacing: -0.02em; }
  .fl-hero p { color: #dbe8fb; font-size: 1.15rem; margin: 0; }
  .fl-hero::after { content: ""; position: absolute; inset: 0; pointer-events: none;
                    background-image: linear-gradient(rgba(255,255,255,.06) 1px, transparent 1px),
                                      linear-gradient(90deg, rgba(255,255,255,.06) 1px, transparent 1px);
                    background-size: 34px 34px; mask-image: linear-gradient(90deg, transparent, #000 70%); }

  .fl-stat { border: 1px solid rgba(127,127,127,.22); border-radius: .8rem; padding: .9rem 1.1rem;
             background: rgba(127,127,127,.04); margin-bottom: .6rem; min-height: 5.9rem; }
  .fl-stat .v { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.15; }
  .fl-stat .l { font-size: .84rem; opacity: .78; margin-top: .15rem; }
  .fl-stat .s { font-size: .76rem; opacity: .6; margin-top: .1rem; }

  .fl-feat { border-radius: .9rem; padding: 1rem 1.1rem; height: 100%; border: 1px solid transparent; }
  .fl-feat .i { font-size: 1.5rem; line-height: 1; }
  .fl-feat b { display: block; font-size: 1rem; margin: .55rem 0 .2rem; }
  .fl-feat span { font-size: .86rem; opacity: .82; line-height: 1.4; }
  .fl-feat.c1 { background: rgba(42,120,214,.12); border-color: rgba(42,120,214,.35); }
  .fl-feat.c2 { background: rgba(27,175,122,.12); border-color: rgba(27,175,122,.35); }
  .fl-feat.c3 { background: rgba(235,104,52,.12); border-color: rgba(235,104,52,.35); }
  .fl-feat.c4 { background: rgba(98,80,214,.12); border-color: rgba(98,80,214,.35); }

  .fl-show-head { display: flex; align-items: center; gap: .6rem; margin-bottom: .2rem; }
  .fl-show-head .tag { background: #eda100; color: #0b0b0b; font-weight: 700; font-size: .72rem; letter-spacing: .06em;
                       text-transform: uppercase; padding: .2rem .55rem; border-radius: .35rem; }
  .fl-q { font-size: .95rem; padding: .7rem .9rem; border-radius: .8rem .8rem .8rem .2rem; background: rgba(42,120,214,.12);
          margin: .5rem 0 .7rem; }
  .fl-a { font-size: 1.1rem; line-height: 1.6; font-weight: 500; }

  .fl-chip { display: inline-block; padding: .18rem .62rem; border-radius: 999px; font-weight: 600;
             font-size: .8rem; letter-spacing: .01em; white-space: nowrap; }
  .fl-badge { display: inline-block; padding: .16rem .6rem; border-radius: .4rem; font-size: .8rem;
              border: 1px solid #0ca30c; margin-right: .35rem; white-space: nowrap; }
  .fl-badge.ex { border-color: rgba(127,127,127,.35); opacity: .8; }
  .fl-check { display: flex; gap: .3rem; flex-wrap: wrap; margin: .2rem 0 .9rem; }
  .fl-check .c { flex: 1 1 9rem; border-left: 3px solid #2a78d6; padding: .35rem .6rem; font-size: .8rem;
                 background: rgba(127,127,127,.05); border-radius: 0 .4rem .4rem 0; line-height: 1.35; }
  .fl-check .c b { display: block; font-size: .95rem; }
  .fl-check .c.good { border-left-color: #0ca30c; }
  .fl-check .c.bad { border-left-color: #d03b3b; }
  .fl-quote { border-left: 3px solid #eda100; padding: .5rem .9rem; margin: .3rem 0 .7rem;
              background: rgba(237,161,0,.09); border-radius: 0 .4rem .4rem 0; font-size: .95rem; }
  .fl-answer { font-size: 1.06rem; line-height: 1.62; }
  .fl-muted { opacity: .68; font-size: .86rem; }
  .fl-section { font-size: .78rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase;
                opacity: .6; margin: 1.6rem 0 .5rem; }
  .fl-verify-t { font-size: .74rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; opacity: .65;
                 margin: .3rem 0 .35rem; }
  .fl-rule { border-left: 3px solid #2a78d6; background: rgba(42,120,214,.08); padding: .7rem .95rem;
             border-radius: 0 .5rem .5rem 0; font-size: .95rem; line-height: 1.5; }
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


@st.cache_data(ttl=3600, show_spinner=False)
def doc_info(document_id: int) -> dict | None:
    try:
        return get_json(f"/doc/{document_id}/info")
    except requests.RequestException:
        return None


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
        st.caption("Page image not in this download. The quote was still checked against the filing's text.")


def verify_box(document_id: int | None, page: int | None, quote_text: str | None = None, path: str | None = None,
               source_url: str | None = None, stacked: bool = False) -> None:
    """Everything a reader needs to check a citation without trusting this app: the PDF opened at the cited
    page, the original filing on the exchange's website, and the exact quote to search for."""
    info = doc_info(document_id) if document_id else None
    src = source_url or (info or {}).get("source_url")
    src = src.replace("/AttachLive/", "/AttachHis/") if src else src  # BSE retires AttachLive links
    if document_id and (info or {}).get("has_pdf"):
        local = f"{API}/doc/{document_id}/pdf#page={page or 1}"
    elif path:
        local = f"{API}/evidence/pdf?path={quote(path)}#page={page or 1}"
    else:
        local = None
    if not (local or src or quote_text):
        return
    with st.container(border=True):
        st.markdown('<div class="fl-verify-t">Verify it yourself</div>', unsafe_allow_html=True)
        slots = [st.container(), st.container()] if stacked else st.columns(2)
        if local:
            slots[0].link_button(f"📄 Open the PDF at page {page}", local, width="stretch",
                                 help="Opens the filing in your browser's PDF viewer, at the cited page.")
        if src:
            slots[1].link_button("🔗 See the original filing on BSE", src, width="stretch",
                                 help="The same document on the exchange's website. BSE can be slow; the PDF button "
                                      "opens an identical copy.")
        if quote_text:
            st.caption("Copy the quote, then search for it in the PDF (Ctrl+F):")
            st.code(" ".join(quote_text.split()), language=None, wrap_lines=True)


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


def next_step(page, label: str, blurb: str) -> None:
    """A clear way on at the bottom of every page, so the app reads as a guided tour."""
    st.divider()
    a, b = st.columns([3, 1], vertical_alignment="center")
    a.markdown(f"**Next:** {blurb}")
    if b.button(label, type="primary", width="stretch", key=f"next-{label}"):
        st.switch_page(page)


def open_flag(symbol: str, code: str) -> None:
    st.session_state["flag"] = (symbol, code)
    st.switch_page(FLAG)


try:
    OVERVIEW = get_json("/overview")
except requests.RequestException as e:
    st.error(f"The FilingLens API isn't reachable at {API}. Start it with `uvicorn api.main:app`. ({e})")
    st.stop()
INDEXED = set(OVERVIEW["symbols_indexed"])


def screen_df() -> pd.DataFrame:
    df = pd.DataFrame(get_json("/screen"))
    df["Q&A"] = df["symbol"].isin(INDEXED)
    return df


def no_key_banner() -> None:
    if not OVERVIEW["has_api_key"]:
        st.info(f"Running without an API key: red flags, the dashboard and the {OVERVIEW['cached_answers']} example "
                "answers all work. Add `LLM_API_KEY` to `.env` to ask your own questions.", icon="🔑")


# ------------------------------------------------------------------------------------------- charts
def bar_chart(df: pd.DataFrame, x: str, y: str, x_title: str, tooltip: list, color: alt.Color | None = None,
              step: int = 24, sort="-x") -> alt.Chart:
    """Horizontal bars, thin, 4px rounded data end, recessive grid; a tooltip on every bar."""
    enc = dict(x=alt.X(f"{x}:Q", title=x_title, axis=alt.Axis(grid=True, tickMinStep=1)),
               y=alt.Y(f"{y}:N", sort=sort, title=None,
                       axis=alt.Axis(labelOverlap=False, labelLimit=260, ticks=False, domain=False)),
               tooltip=tooltip)
    if color is not None:
        enc["color"] = color
    # Left padding: labels are measured before the web font loads and drawn wider after, which clipped first letters.
    return (alt.Chart(df).mark_bar(cornerRadiusEnd=4, size=14).encode(**enc)
            .properties(height=alt.Step(step), padding={"left": 24, "top": 5, "right": 5, "bottom": 5}))


LEVEL_COLOR = alt.Color("level:N", title="Risk level",
                        scale=alt.Scale(domain=["low", "elevated", "high"],
                                        range=[LEVEL[k]["fill"] for k in ("low", "elevated", "high")]),
                        legend=alt.Legend(orient="top", labelExpr="upper(slice(datum.label,0,1)) + slice(datum.label,1)"))


def shareholding_chart(symbol: str, height: int = 360, narrow: bool = False) -> alt.Chart | None:
    rows = get_json(f"/shareholding/{symbol}")
    if len(rows) < 2:
        return None
    df = pd.DataFrame(rows)
    if not (df["promoter_pct"].fillna(0) > 0).any():
        return None  # no identified promoter (e.g. YES Bank): a flat zero line would only mislead
    df["as_of"] = pd.to_datetime(df["as_of"])
    p_lab, e_lab = "Promoter holding (% of company)", "Encumbered (% of promoter holding)"
    long = df.melt(id_vars=["period", "as_of"], value_vars=["promoter_pct", "encumbered_pct_of_promoter"],
                   var_name="series", value_name="pct").dropna(subset=["pct"])
    long["series"] = long["series"].map({"promoter_pct": p_lab, "encumbered_pct_of_promoter": e_lab})
    base = alt.Chart(long).encode(
        x=alt.X("as_of:T", title=None, axis=alt.Axis(format="%b %Y", grid=False, labelAngle=0)),
        y=alt.Y("pct:Q", title="Percent", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("series:N", title=None, scale=alt.Scale(domain=[p_lab, e_lab]),
                        legend=alt.Legend(orient="top", direction="vertical" if narrow else "horizontal", labelLimit=340)),
        tooltip=[alt.Tooltip("period:N", title="Quarter"), alt.Tooltip("series:N", title="Series"),
                 alt.Tooltip("pct:Q", title="Percent", format=".2f")])
    layers = []
    enc = df.dropna(subset=["encumbered_pct_of_promoter"]).sort_values("as_of")
    rises = [{"start": a, "end": b} for (a, x), (b, y) in zip(
        enc[["as_of", "encumbered_pct_of_promoter"]].itertuples(index=False),
        enc[["as_of", "encumbered_pct_of_promoter"]].iloc[1:].itertuples(index=False)) if y > x + 0.5]
    if rises:  # shade the quarters where encumbrance rose, so the trend reads at a glance
        layers.append(alt.Chart(pd.DataFrame(rises)).mark_rect(opacity=0.13, color="#eb6834")
                      .encode(x="start:T", x2="end:T"))
    layers += [base.mark_line(strokeWidth=2.5), base.mark_circle(size=70)]
    return alt.layer(*layers).properties(height=height)


# ------------------------------------------------------------------------------------------- pages
def landing() -> None:
    """Splash: one bold statement over a living background, and one way in."""
    st.markdown("""
<style>
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], header[data-testid="stHeader"] { display: none !important; }
  .block-container { padding: 0 !important; max-width: 100% !important; }
  .fl-land { position: fixed; inset: 0; z-index: 1000; overflow: hidden; display: grid; place-items: center;
             background: radial-gradient(1200px 600px at 70% 20%, rgba(42,120,214,.55), transparent 60%),
                         radial-gradient(900px 500px at 15% 85%, rgba(98,80,214,.35), transparent 60%),
                         linear-gradient(160deg, #050d1f 0%, #0b2a55 55%, #0d366b 100%); }
  .fl-land .grid { position: absolute; inset: -50%; background-image:
                     linear-gradient(rgba(134,182,239,.08) 1px, transparent 1px),
                     linear-gradient(90deg, rgba(134,182,239,.08) 1px, transparent 1px);
                   background-size: 48px 48px; transform: perspective(700px) rotateX(58deg);
                   animation: fl-pan 18s linear infinite; mask-image: radial-gradient(closest-side, #000 40%, transparent); }
  @keyframes fl-pan { from { background-position: 0 0; } to { background-position: 0 480px; } }
  .fl-land .page { position: absolute; width: 170px; height: 220px; border-radius: 10px; background: rgba(255,255,255,.05);
                   border: 1px solid rgba(255,255,255,.12); backdrop-filter: blur(2px); padding: 22px 18px;
                   animation: fl-float 9s ease-in-out infinite; }
  .fl-land .page i { display: block; height: 7px; border-radius: 4px; background: rgba(255,255,255,.16); margin: 0 0 11px; }
  .fl-land .page i.hl { background: rgba(237,161,0,.85); box-shadow: 0 0 18px rgba(237,161,0,.6);
                        animation: fl-glow 3.2s ease-in-out infinite; }
  .fl-land .p1 { left: 8%; top: 18%; transform: rotate(-9deg); }
  .fl-land .p2 { right: 9%; top: 14%; transform: rotate(8deg); animation-delay: -3s; }
  .fl-land .p3 { right: 16%; bottom: 10%; transform: rotate(-5deg); animation-delay: -6s; }
  .fl-land .p4 { left: 14%; bottom: 9%; transform: rotate(6deg); animation-delay: -4.5s; }
  @keyframes fl-float { 0%,100% { translate: 0 0; } 50% { translate: 0 -14px; } }
  @keyframes fl-glow { 0%,100% { opacity: .55; } 50% { opacity: 1; } }
  .fl-land .center { position: relative; text-align: center; color: #fff; padding: 0 24px; z-index: 2; }
  .fl-land .kicker { display: inline-block; font-size: .8rem; letter-spacing: .14em; text-transform: uppercase;
                     color: #b7d3f6; border: 1px solid rgba(183,211,246,.35); border-radius: 999px; padding: .3rem .9rem; }
  .fl-land h1 { color: #fff !important; font-size: clamp(3rem, 9vw, 6.4rem); line-height: 1; margin: 1.2rem 0 .9rem;
                letter-spacing: -0.045em; font-weight: 800; text-shadow: 0 10px 50px rgba(42,120,214,.55); }
  .fl-land h1 span { background: linear-gradient(90deg, #ffffff 30%, #86b6ef); -webkit-background-clip: text;
                     background-clip: text; color: transparent; }
  .fl-land p { color: #cde2fb; font-size: clamp(1.05rem, 2vw, 1.35rem); max-width: 40rem; margin: 0 auto 2.2rem; line-height: 1.5; }
  .fl-land a.cta { display: inline-block; background: #fff; color: #0b2a55 !important; font-weight: 700; font-size: 1.05rem;
                   padding: .95rem 2.1rem; border-radius: 999px; text-decoration: none !important;
                   box-shadow: 0 10px 40px rgba(42,120,214,.55); transition: transform .15s ease, box-shadow .15s ease; }
  .fl-land a.cta:hover { transform: translateY(-2px); box-shadow: 0 14px 50px rgba(42,120,214,.75); }
  .fl-land .foot { position: absolute; bottom: 22px; left: 0; right: 0; text-align: center; color: rgba(205,226,251,.55);
                   font-size: .8rem; z-index: 2; }
</style>
<div class="fl-land">
  <div class="grid"></div>
  <div class="page p1"><i></i><i style="width:80%"></i><i class="hl" style="width:90%"></i><i></i><i style="width:60%"></i><i></i><i style="width:70%"></i></div>
  <div class="page p2"><i style="width:70%"></i><i></i><i></i><i class="hl" style="width:75%"></i><i style="width:85%"></i><i></i></div>
  <div class="page p3"><i></i><i class="hl" style="width:65%"></i><i style="width:90%"></i><i></i><i style="width:50%"></i></div>
  <div class="page p4"><i style="width:85%"></i><i></i><i style="width:60%"></i><i></i><i class="hl" style="width:80%"></i></div>
  <div class="center">
    <span class="kicker">Indian company filings · NSE &amp; BSE</span>
    <h1><span>FilingLens</span></h1>
    <p>Ask any listed company's filings. Get answers you can check, line by line, on the page they came from.</p>
    <a class="cta" href="./home" target="_self">Learn more →</a>
  </div>
  <div class="foot">Research tool · Not investment advice</div>
</div>""", unsafe_allow_html=True)


def home() -> None:
    st.markdown("""
    <div class="fl-hero">
      <h1>Every answer, proven on the page.</h1>
      <p>Ask about any company's filings. FilingLens shows you exactly where the answer came from.</p>
    </div>""", unsafe_allow_html=True)
    no_key_banner()

    o = OVERVIEW
    df = screen_df()
    flagged = int((df["score"] > 0).sum())
    tiles = [(f"{o['pdfs_in_corpus']:,}", "Filings collected", "44 stocks since April 2023")] if o["pdfs_in_corpus"] else []
    tiles += [
              (f"{o['filings_indexed']:,}", "Filings you can question", f"{len(o['symbols_indexed'])} stocks"),
              (f"{o['symbols_screened']}", "Stocks screened for red flags", f"{flagged} flagged"),
              # A property, not a score: only citations that pass the checker are ever shown.
              ("✓", "Every citation checked", "against its page before you see it")]
    # The corpus count comes from data/raw/manifest.jsonl; if a download lacks it, drop the tile rather than show "—".
    for c, (v, l, s) in zip(st.columns(len(tiles)), tiles):
        c.markdown(stat(v, l, s), unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    if c1.button("💬  Ask the filings", type="primary", width="stretch"):
        st.switch_page(ASK)
    if c2.button("📊  Open the risk dashboard", width="stretch"):
        st.switch_page(DASH)

    st.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)
    feats = [("c1", "🔎", "Finds the right page", "Keyword plus meaning search."),
             ("c2", "🛡️", "Every quote checked", "No match in the filing, no citation."),
             ("c3", "🚩", "Red flags, no guesswork", "Clear rules over filed data."),
             ("c4", "📄", "See it yourself", "Highlighted on the original PDF.")]
    for c, (cls, ico, t, p) in zip(st.columns(4), feats):
        c.markdown(f'<div class="fl-feat {cls}"><div class="i">{ico}</div><b>{t}</b><span>{p}</span></div>',
                   unsafe_allow_html=True)

    section("See it in action")
    exs = get_json("/examples")
    ex = next((e for e in exs if e["symbol"] == "RELIANCE" and "Digital" in e["question"]), None) or (exs or [None])[0]
    a = cached_answer(ex["symbol"], ex["question"]) if ex else None
    if a and a.get("citations"):
        c = a["citations"][0]
        with st.container(border=True):
            left, right = st.columns([2, 3], gap="large")
            with left:
                st.markdown('<div class="fl-show-head"><span class="tag">Verified</span>'
                            f'<b>{ex["symbol"]}</b></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="fl-q">💬 {ex["question"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="fl-a">{a["answer"]}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="fl-quote" style="margin-top:1rem">“{" ".join(c["quote"].split())}”</div>',
                            unsafe_allow_html=True)
                st.caption(f"{DOC_TYPES.get(c['doc_type'], c['doc_type'])} · {c.get('fiscal_period') or ''} · Page {c['page']} "
                           "· The highlighted line on the right is the source.")
                verify_box(c["document_id"], c["page"], stacked=True)
            with right:
                show_page(page_url(c["document_id"], c["page"], hl=c["quote"]))

    section("What you can ask about")
    types = pd.DataFrame([{"type": DOC_TYPES.get(k, k.title()), "filings": v} for k, v in o["doc_types"].items()])
    st.altair_chart(bar_chart(types, "filings", "type", "Filings",
                              [alt.Tooltip("type:N", title="Type"), alt.Tooltip("filings:Q", title="Filings")], step=30),
                    width="stretch")

    next_step(DASH, "Dashboard →", "See which of the 44 stocks the rule engine flags, and why.")
    st.markdown('<p class="fl-muted">Research and investor-protection tool. Not investment advice. '
                'No trading, no broker keys, public filings only.</p>', unsafe_allow_html=True)


def flag_cards(symbol: str, f: dict, key: str) -> None:
    """One clickable card per flag; the detail opens on its own page."""
    if not f["flags"]:
        st.success("No red flags in the available filings.", icon="✅")
        return
    for fl in f["flags"]:
        icon = "🔴" if fl["severity"] == 3 else "🟠"
        with st.container(border=True):
            a, b = st.columns([3, 1], vertical_alignment="center")
            a.markdown(f"**{icon} {fl['title']}**")
            a.caption(fl["detail"][:150] + ("…" if len(fl["detail"]) > 150 else ""))
            if b.button("Details →", key=f"{key}-{fl['code']}", width="stretch"):
                open_flag(symbol, fl["code"])


# ------------------------------------------------------------------------------------------- brief
st.markdown("""
<style>
  .fl-why { display: inline-block; padding: .14rem .55rem; border-radius: .4rem; font-size: .78rem; line-height: 1.35;
            background: rgba(237,161,0,.14); border: 1px solid rgba(237,161,0,.45); margin: .1rem 0 .35rem; }
  .fl-why.act { background: rgba(98,80,214,.12); border-color: rgba(98,80,214,.4); }
  .fl-brief-h { font-size: 1.12rem; font-weight: 700; margin: 0 0 .35rem; letter-spacing: -0.01em; }
  .fl-nc { opacity: .75; font-style: italic; margin: .2rem 0 .3rem; }
</style>
""", unsafe_allow_html=True)

# A question with no number, no period and no financial metric reads as "tell me about this company":
# the Brief answers that better. Detected by the ABSENCE of specifics; the question is still answered.
_SPECIFIC_RE = re.compile(
    r"\d|\b(q[1-4]|h[12]|fy|quarter|quarterly|year|annual|month|latest|since|ytd|yoy|qoq)\b|"
    r"\b(revenue|sales|turnover|ebitda|ebit|margin|profit|loss|pat|eps|earnings|income|expense|cost|debt|borrowing|"
    r"cash|capex|nim|npa|gnpa|nnpa|casa|deposit|advances|loan|credit|asset|roa|roe|roce|ratio|dividend|guidance|"
    r"outlook|arpu|subscriber|user|growth|pledge|encumb|shareholding|stake|promoter|auditor|audit|rating|interest|"
    r"tax|order book|capacity|volume|segment|subsidiary|provision|impairment|valuation|price|bond|at1|court|"
    r"penalty|merger|acquisition|buyback|dilution|capital|liquidity|exposure|write-?off|tariff|spectrum|agr)\w*",
    re.I)


def is_general_question(q: str) -> bool:
    return bool((q or "").strip()) and not _SPECIFIC_RE.search(q)


def nice_date(d: str | None) -> str:
    try:
        t = pd.Timestamp(d)
        return f"{t.day} {t.strftime('%b %Y')}"
    except (ValueError, TypeError):
        return d or ""


def strip_refs(text: str) -> str:
    """The model's [c3]-style excerpt markers mean nothing to a reader; the citations are listed below."""
    return " ".join(re.sub(r"\s*\[c\d+\](?:\s*,\s*\[c\d+\])*", "", text or "").split())


def brief_citations(cits: list[dict], key: str) -> None:
    tabs = st.tabs([f"[{i}] {DOC_TYPES.get(c['doc_type'], c['doc_type'])} · {c.get('fiscal_period') or '—'} · Page {c['page']}"
                    for i, c in enumerate(cits, 1)])
    for tab, c in zip(tabs, cits):
        with tab:
            left, right = st.columns([2, 3], gap="large")
            with left:
                st.markdown(f'<div class="fl-quote">“{html.escape(" ".join(c["quote"].split()))}”</div>',
                            unsafe_allow_html=True)
                st.caption(f"{c['title'][:140]} · Page {c['page']}")
                verify_box(c["document_id"], c["page"], c["quote"], stacked=True)
            with right:
                show_page(page_url(c["document_id"], c["page"], hl=c["quote"]))


def notable_card(it: dict, key: str) -> None:
    with st.container(border=True):
        a, b = st.columns([3, 2], gap="large")
        s = it.get("sentence")
        c = s["citations"][0] if s and s.get("covered") and s.get("citations") else None
        with a:
            st.markdown(f"**{html.escape(it.get('display') or it['title'])}**")
            st.caption(f"{nice_date(it['filed_on'])} · {DOC_TYPES.get(it['doc_type'], it['doc_type'])}")
            st.markdown(f'<span class="fl-why">{html.escape(it["why"])}</span>', unsafe_allow_html=True)
            if c:
                st.markdown(html.escape(strip_refs(s["text"])))
                st.markdown(f'<div class="fl-quote">“{html.escape(" ".join(c["quote"].split()))}”</div>',
                            unsafe_allow_html=True)
                with st.expander(f"See it highlighted on page {c['page']}"):
                    show_page(page_url(c["document_id"], c["page"], hl=c["quote"]))
        with b:
            if c:
                verify_box(c["document_id"], c["page"], c["quote"], stacked=True)
            else:
                verify_box(it.get("document_id"), it.get("page") or 1, path=it.get("path"),
                           source_url=it.get("source_url"), stacked=True)


def brief_view(symbol: str, key: str) -> None:
    """The Brief: four short sections where every line is a verified citation or says the filings don't cover it."""
    live_key = f"brief-live-{symbol}"
    try:
        b = st.session_state.get(live_key) or get_json(f"/brief/{symbol}")
    except requests.RequestException:
        st.error("The brief couldn't be loaded. Please try again.")
        return
    secs = {s["key"]: s for s in b["sections"]}
    n_cit = sum(len(s.get("citations") or []) for s in b["sections"] if s.get("covered"))
    n_cit += sum(len((i.get("sentence") or {}).get("citations") or []) for i in secs["notable"]["notable"]["items"])
    badges = []
    if n_cit:
        badges.append(f'<span class="fl-badge">✓ {n_cit} verified citation{"s" if n_cit != 1 else ""}</span>')
    if b.get("cached"):
        badges.append('<span class="fl-badge ex">⚡ Prepared brief</span>')
    if badges:
        st.markdown("".join(badges), unsafe_allow_html=True)
    st.markdown(f'<p class="fl-muted" style="margin-top:.4rem">Every line is a quote checked against {symbol}\'s filings, '
                'or says plainly that the filings don\'t cover it.</p>', unsafe_allow_html=True)

    for sec in b["sections"]:
        with st.container(border=True):
            st.markdown(f'<div class="fl-brief-h">{sec["title"]}</div>', unsafe_allow_html=True)
            if "question" in sec:
                if not sec.get("prepared"):
                    st.markdown('<div class="fl-nc">Not prepared for this stock yet.</div>', unsafe_allow_html=True)
                    if sec["key"] == "quarter":  # one button prepares both cited sections
                        if OVERVIEW["has_api_key"] and symbol in INDEXED:
                            if st.button("Prepare the cited sections now", key=f"prep-{key}-{symbol}",
                                         help="Answers both questions from the filings and checks every quote. "
                                              "Takes about a minute."):
                                with st.spinner("Finding the right pages and checking every quote…"):
                                    r = requests.get(f"{API}/brief/{symbol}", params={"live": "true"}, timeout=400)
                                if r.ok:
                                    st.session_state[live_key] = r.json()
                                    st.rerun()
                                st.error(f"Something went wrong ({r.status_code}). Please try again.")
                        elif symbol not in INDEXED:
                            st.caption("This stock's filings aren't in the search index yet, so only the rule-based "
                                       "sections are available.")
                        else:
                            st.caption("Add `LLM_API_KEY` to `.env` to prepare these sections.")
                    continue
                if not sec["covered"]:
                    st.markdown('<div class="fl-nc">The filings don\'t cover this.</div>', unsafe_allow_html=True)
                    st.caption(f"Asked: {sec['question']}")
                    continue
                st.markdown(f'<div class="fl-answer">{html.escape(strip_refs(sec["text"]))}</div>', unsafe_allow_html=True)
                if sec.get("period_note"):
                    st.caption("🗓️ " + sec["period_note"])
                brief_citations(sec["citations"], key=f"{key}-{sec['key']}")
            elif sec["key"] == "flags":
                f = sec["flags"]
                st.markdown(f"{chip(f['level'])} <span class='fl-muted'>&nbsp;Score {f['score']} · "
                            f"{len(f['flags'])} flag(s), from clear rules over filed data</span>", unsafe_allow_html=True)
                st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
                flag_cards(symbol, f, key=f"brief-{key}-{symbol}")
            else:
                nb = sec["notable"]
                if not (nb["items"] or nb["activity"]):
                    st.markdown('<div class="fl-nc">The filings don\'t cover this: nothing unusual stands out in '
                                f'{symbol}\'s filings.</div>', unsafe_allow_html=True)
                for it in nb["items"]:
                    notable_card(it, key)
                act = nb.get("activity")
                if act:
                    with st.container(border=True):
                        st.markdown("**Unusual filing activity**")
                        st.markdown(f'<span class="fl-why act">{html.escape(act["why"])}</span>', unsafe_allow_html=True)
                        st.caption("Filed in that stretch: " + "; ".join(t[:90] for t in act["titles"]))
                st.caption("Ranked by how rare each filing is across all 44 companies, plus labels for legal, credit, "
                           "auditor and leadership events. Routine paperwork is pushed down. No AI involved.")


def brief_page() -> None:
    st.title("Brief")
    st.markdown('<p class="fl-muted">A cited summary of any company: the latest quarter, what management said, '
                'red flags and notable filings.</p>', unsafe_allow_html=True)
    no_key_banner()
    syms = sorted(set(screen_df()["symbol"]) | INDEXED)
    pre = st.session_state.pop("brief_symbol", None) or st.session_state.get("symbol")
    pre = pre if pre in syms else ("YESBANK" if "YESBANK" in syms else syms[0])
    symbol = st.selectbox("Stock", syms, index=syms.index(pre))
    st.session_state["symbol"] = symbol
    brief_view(symbol, key="page")
    if symbol in INDEXED and st.button(f"Ask your own question about {symbol} →", type="primary"):
        st.session_state["ask_symbol"] = symbol
        st.switch_page(ASK)
    next_step(DASH, "Dashboard →", "See which of the 44 stocks the rule engine flags, and why.")


def dashboard() -> None:
    st.title("Risk dashboard")
    st.markdown('<p class="fl-muted">Red flags from clear rules over shareholding patterns, auditor filings and prices. '
                'Click any flag to see the evidence.</p>', unsafe_allow_html=True)
    df = screen_df()
    flagged = df[df["score"] > 0]
    for c, (v, l, s) in zip(st.columns(4), [
        (len(df), "Stocks screened", "NSE/BSE listed"),
        (len(flagged), "Flagged", f"{len(df) - len(flagged)} with no flags"),
        (int((df["level"] == "high").sum()), "High risk", "Score 5 or more"),
        (int(df["Q&A"].sum()), "Searchable in Ask", "Question their filings")]):
        c.markdown(stat(v, l, s), unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        section("Risk score by stock (flagged only)")
        fd = flagged.assign(flag_names=flagged["flags"].map(lambda fs: ", ".join(FLAG_NAMES.get(f, f) for f in fs)),
                            Level=flagged["level"].map(lambda k: LEVEL[k]["label"]))
        st.altair_chart(bar_chart(fd, "score", "symbol", "Risk score (sum of flag severities)",
                                  [alt.Tooltip("symbol:N", title="Stock"), alt.Tooltip("score:Q", title="Score"),
                                   alt.Tooltip("Level:N", title="Level"), alt.Tooltip("flag_names:N", title="Flags")],
                                  color=LEVEL_COLOR), width="stretch")
    with right:
        section("How often each rule fires")
        counts = pd.Series([FLAG_NAMES.get(f, f) for fs in df["flags"] for f in fs]).value_counts()
        fc = pd.DataFrame({"flag": counts.index, "stocks": counts.values})
        st.altair_chart(bar_chart(fc, "stocks", "flag", "Stocks flagged",
                                  [alt.Tooltip("flag:N", title="Rule"), alt.Tooltip("stocks:Q", title="Stocks")]),
                        width="stretch")
        st.markdown('<p class="fl-muted">Score is the sum of flag severities. Auditor exits and qualified opinions '
                    'weigh the most.</p>', unsafe_allow_html=True)

    section("All stocks")
    levels = st.pills("Level", ["high", "elevated", "low"], selection_mode="multi", default=["high", "elevated", "low"],
                      format_func=lambda k: f"{LEVEL[k]['icon']} {LEVEL[k]['label']}", label_visibility="collapsed")
    table = df[df["level"].isin(levels or [])].copy()
    table["Level"] = table["level"].map(lambda k: f"{LEVEL[k]['icon']} {LEVEL[k]['label']}")
    table["Flags"] = table["flags"].map(lambda fs: ", ".join(FLAG_NAMES.get(f, f) for f in fs) or "—")
    # Text, not a checkbox: a checkbox column looks clickable but isn't.
    table["In Ask"] = table["Q&A"].map(lambda v: "Yes" if v else "—")
    st.dataframe(table[["symbol", "Level", "score", "Flags", "In Ask"]], hide_index=True, width="stretch",
                 column_config={"symbol": st.column_config.TextColumn("Stock"),
                                "score": st.column_config.ProgressColumn("Score", min_value=0,
                                                                         max_value=int(df["score"].max() or 1), format="%d"),
                                "In Ask": st.column_config.TextColumn("In Ask", help="Can you question this "
                                                                      "company's filings on the Ask page?")})

    section("Stock detail")
    default = st.session_state.get("symbol", "IDEA")
    syms = list(df["symbol"])
    sym = st.selectbox("Stock", syms, index=syms.index(default) if default in syms else 0, label_visibility="collapsed")
    st.session_state["symbol"] = sym
    f = get_json(f"/flags/{sym}")
    st.markdown(f"### {sym} &nbsp; {chip(f['level'])} <span class='fl-muted'>Score {f['score']}</span>", unsafe_allow_html=True)
    ch = shareholding_chart(sym, height=380)
    if ch is not None:
        st.altair_chart(ch, width="stretch")
        st.caption("Promoter holding and the share of it that is encumbered, each quarter (NSE shareholding pattern). "
                   "Shaded: quarters where encumbrance rose.")
    else:
        st.caption("No promoter shareholding series for this stock (for example, it has no identified promoter).")
    flag_cards(sym, f, key=f"dash-{sym}")
    if st.button("View the Brief →", key=f"brief-{sym}", help=f"A cited summary of {sym}: latest quarter, management, "
                                                             "red flags and notable filings."):
        st.session_state["brief_symbol"] = sym
        st.switch_page(BRIEF)
    if sym in INDEXED and st.button(f"Ask about {sym}'s filings →", type="primary"):
        st.session_state["ask_symbol"] = sym
        st.switch_page(ASK)
    next_step(ASK, "Ask →", "Question a company's filings and check every answer against its page.")


def flag_page() -> None:
    sel = st.session_state.get("flag")
    if not sel:
        st.info("Pick a flag on the dashboard to see its evidence.")
        if st.button("← Back to dashboard"):
            st.switch_page(DASH)
        return
    symbol, code = sel
    f = get_json(f"/flags/{symbol}")
    fl = next((x for x in f["flags"] if x["code"] == code), None)
    if st.button("← Back to dashboard"):
        st.session_state["symbol"] = symbol
        st.switch_page(DASH)
    if not fl:
        st.warning(f"{symbol} no longer triggers this flag.")
        return
    # Severity of this one flag (adds to the stock's score); the dashboard's High/Elevated/Low is the stock's level.
    sev = ("🔴 High-severity flag" if fl["severity"] == 3 else "🟠 Medium-severity flag") + \
        f" · adds {fl['severity']} to the risk score"
    st.markdown(f"<p class='fl-muted' style='margin:0'>{symbol} · {sev}</p>", unsafe_allow_html=True)
    st.title(fl["title"])

    section("What this rule checks")
    st.markdown(f'<div class="fl-rule">{rule_text(code, f["thresholds"])}</div>', unsafe_allow_html=True)
    section("What we found")
    st.markdown(f'<div class="fl-answer">{fl["detail"]}</div>', unsafe_allow_html=True)

    structured = [e for e in fl["evidence"] if e.get("values")]
    if structured:
        section("The filed numbers")
        rows = [{"period": e.get("period") or e.get("as_of"), **e["values"]} for e in structured]
        # One row per measure, one column per period: fits the page instead of scrolling sideways.
        tdf = (pd.DataFrame(rows).set_index("period").rename(columns=VALUE_LABELS).T
               .reset_index().rename(columns={"index": "Measure"}))
        tdf.columns = [str(c) for c in tdf.columns]
        st.dataframe(tdf, hide_index=True, width="stretch")
        if code in ("pledge_creep", "pledge_high", "promoter_stake_drop"):
            ch = shareholding_chart(symbol, height=340)
            if ch is not None:
                st.altair_chart(ch, width="stretch")
        raw = [e for e in structured if e.get("source_url")]
        if raw:
            # NSE's readable shareholding page first; the raw XBRL files (what the numbers above are read from)
            # stay available, labelled as machine-readable data so nobody mistakes the XML for a broken page.
            st.link_button("📊 Shareholding filings on NSE", width="content",
                           url=f"https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern?symbol={quote(symbol)}",
                           help="NSE's page listing this company's quarterly shareholding patterns, in readable form.")
            st.caption("Raw filed data, machine-readable XBRL (the numbers above are read from these files): " + " · ".join(
                f"[{e.get('period') or e.get('as_of') or 'filing'}]({e['source_url']})" for e in raw))

    docs = [e for e in fl["evidence"] if e.get("page") and (e.get("document_id") or e.get("path"))]
    if docs:
        section("Evidence in the filings")
        for e in docs:
            with st.container(border=True):
                when = " · ".join(x for x in (e.get("period"), e.get("as_of")) if x)
                st.markdown(f"**{(e.get('title') or 'Filing')[:140]}**")
                if when:
                    st.caption(f"{when} · Page {e['page']}")
                q = clean_quote(e.get("quote"))
                if q and " ".join(q.strip("…").split()).lower() not in " ".join(fl["detail"].split()).lower():
                    st.markdown(f'<div class="fl-quote">{q}</div>', unsafe_allow_html=True)
                a, b = st.columns([3, 2], gap="large")
                with a:
                    show_page(page_url(e.get("document_id"), e.get("page"), e.get("path")))
                with b:
                    verify_box(e.get("document_id"), e.get("page"), path=e.get("path"),
                               source_url=e.get("source_url"), stacked=True)

    st.divider()
    a, b = st.columns(2)
    if a.button("← Back to dashboard", key="back2", width="stretch"):
        st.session_state["symbol"] = symbol
        st.switch_page(DASH)
    if symbol in INDEXED and b.button(f"Ask about {symbol} →", type="primary", width="stretch"):
        st.session_state["ask_symbol"] = symbol
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
                    f"<span class='fl-muted'>&nbsp;Score {f['score']} · {len(f['flags'])} flag(s)</span></div>",
                    unsafe_allow_html=True)

    tab_brief, tab_ask = st.tabs(["Brief", "Ask a question"])
    with tab_brief:
        brief_view(symbol, key="ask")
    with tab_ask:
        ask_tab(symbol, f)
    next_step(EVAL, "Evaluation →", "How accurate the answers are, measured on hand-checked questions.")


def brief_offer(symbol: str, question: str) -> None:
    """Offer, never route: the typed question is still answered (or explained) below."""
    if not is_general_question(question):
        return
    with st.container(border=True):
        o1, o2 = st.columns([3, 1], vertical_alignment="center")
        o1.markdown(f"This looks like a general question. Open the Brief for {symbol}?")
        if o2.button("Open the Brief →", key=f"offer-{symbol}", width="stretch"):
            st.session_state["brief_symbol"] = symbol
            st.switch_page(BRIEF)


def ask_tab(symbol: str, f: dict) -> None:
    examples = [e["question"] for e in get_json(f"/examples?symbol={symbol}")]
    if examples:
        picked = st.pills("Try an example", examples, selection_mode="single", key=f"pill-{symbol}")
        if picked and st.session_state.get("last_pill") != picked:
            st.session_state["last_pill"] = picked
            st.session_state["q"] = picked
            st.session_state["pending"] = True

    with st.form("ask", border=False):
        q = st.text_input("Question", key="q",
                          placeholder="E.g. What was the net interest margin in the latest quarter, and why did it move?")
        c1, c2, c3 = st.columns([2, 1, 1], vertical_alignment="bottom")
        doc_type = c1.selectbox("Search in", ["any", "results", "concall", "announcement", "shareholding"],
                                format_func=lambda k: "All filings" if k == "any" else DOC_TYPES.get(k, k))
        live = c2.toggle("Ask live", value=False, disabled=not OVERVIEW["has_api_key"],
                         help="Generate a fresh answer now, even for an example question.")
        submitted = c3.form_submit_button("Ask", type="primary", width="stretch")
    if (submitted or st.session_state.pop("pending", False)) and q:
        with st.spinner("Finding the right pages and checking every quote…"):
            resp = requests.post(f"{API}/ask", json={"symbol": symbol, "question": q, "use_cache": not live,
                                                     "doc_type": None if doc_type == "any" else doc_type}, timeout=180)
        if resp.ok:
            st.session_state["last"] = resp.json()
            st.session_state.pop("nokey", None)
        elif resp.status_code == 503:
            # Kept in session state so the message and the Brief offer survive the rerun a button click causes.
            st.session_state.pop("last", None)
            st.session_state["nokey"] = {"symbol": symbol, "question": q,
                                         "detail": resp.json().get("detail", "No API key set.")}
        else:
            st.error(f"Something went wrong ({resp.status_code}). Please try again.")

    nk = st.session_state.get("nokey")
    if nk and nk["symbol"] == symbol:
        st.info(nk["detail"], icon="🔑")
        # No key: the prepared Brief still covers a general question, so offer it here too.
        brief_offer(symbol, nk["question"])
        return

    r = st.session_state.get("last")
    if not (r and r.get("symbol") == symbol):
        return
    brief_offer(symbol, r.get("question", ""))
    main, side = st.columns([3, 2], gap="large")
    with main:
        n_ok, n_drop = len(r["citations"]), r["dropped_citations"]
        with st.container(border=True):
            badges = []
            if n_ok:
                badges.append(f'<span class="fl-badge">✓ {n_ok} verified citation{"s" if n_ok != 1 else ""}</span>')
            if r.get("cached"):
                badges.append('<span class="fl-badge ex">⚡ Example answer</span>')
            if badges:
                st.markdown("".join(badges), unsafe_allow_html=True)
            st.markdown(f'<div class="fl-answer" style="margin-top:.6rem">{r["answer"]}</div>', unsafe_allow_html=True)
            if r.get("period_note"):
                st.caption("🗓️ " + r["period_note"])

        if not r["abstained"] or n_ok:
            section("How this answer was checked")
            verdict = "good" if n_ok and not n_drop else ("bad" if n_drop else "")
            pages = len({(c["document_id"], c["page"]) for c in r["citations"]})
            st.markdown(f"""
            <div class="fl-check">
              <div class="c"><b>8 passages</b>Found in {symbol}'s filings</div>
              <div class="c"><b>{n_ok + n_drop} quote{"s" if n_ok + n_drop != 1 else ""}</b>Given as evidence</div>
              <div class="c {verdict}"><b>{n_ok} confirmed · {n_drop} rejected</b>Each quote matched word for word</div>
              <div class="c"><b>{pages} page{"s" if pages != 1 else ""}</b>Shown below, line highlighted</div>
            </div>""", unsafe_allow_html=True)

        if r["citations"]:
            tabs = st.tabs([f"[{i}] {DOC_TYPES.get(c['doc_type'], c['doc_type'])} · {c.get('fiscal_period') or '—'} · Page {c['page']}"
                            for i, c in enumerate(r["citations"], 1)])
            for tab, c in zip(tabs, r["citations"]):
                with tab:
                    st.markdown(f'<div class="fl-quote">“{" ".join(c["quote"].split())}”</div>', unsafe_allow_html=True)
                    st.caption(f"{c['title'][:140]} · Page {c['page']}")
                    verify_box(c["document_id"], c["page"], c["quote"])
                    show_page(page_url(c["document_id"], c["page"], hl=c["quote"]))
    with side:
        section(f"{symbol} red flags")
        flag_cards(symbol, f, key=f"ask-{symbol}")
        ch = shareholding_chart(symbol, height=230, narrow=True)
        if ch is not None:
            section("Promoter holding")
            st.altair_chart(ch, width="stretch")


def evaluation() -> None:
    st.title("Evaluation")
    ev = get_json("/eval")
    e2e, abl = ev.get("e2e") or {}, ev.get("ablation") or {}
    st.markdown('<p class="fl-muted">How we test FilingLens: questions with known answers, checked against the filings.</p>',
                unsafe_allow_html=True)

    left, right = st.columns([2, 3], gap="large")
    with left:
        section("The test questions")
        mix = pd.DataFrame([{"type": QTYPES.get(k, k.title()), "questions": v} for k, v in ev["question_types"].items()])
        st.altair_chart(bar_chart(mix, "questions", "type", "Questions",
                                  [alt.Tooltip("type:N", title="Type"), alt.Tooltip("questions:Q", title="Questions")]),
                        width="stretch")
        st.caption(f"{ev['n_questions']} questions across {len(ev['question_symbols'])} stocks.")
    with right:
        head_name = "hybrid+rerank" if "hybrid+rerank" in e2e else next(iter(e2e), None)
        if head_name:
            section("Results")
            h = e2e[head_name]
            g = st.columns(3)
            g[0].markdown(stat(cell(h["accuracy"]), "Answers correct"), unsafe_allow_html=True)
            g[1].markdown(stat(cell(h["citation_precision"]), "Citations on the right page"), unsafe_allow_html=True)
            g[2].markdown(stat(cell(h["abstention"]), "Correctly said “not in the filings”"), unsafe_allow_html=True)
            g = st.columns(3)
            g[0].markdown(stat(cell(h["verifier_rejection"]), "Quotes rejected by the checker"), unsafe_allow_html=True)
            g[1].markdown(stat(cell(h["false_abstention"]), "Missed answers that existed"), unsafe_allow_html=True)
            g[2].markdown(stat(f"{h['median_latency_s']:.1f}s", "Typical answer time"), unsafe_allow_html=True)

    if abl:
        section("How often does FilingLens find the right page?")
        rows = []
        for name, v in abl.items():
            a = v.get("all", {})
            if a.get("n"):
                rows.append({"method": CONFIG_NAMES.get(name, name), "found": round(100 * a["hits"] / a["n"]),
                             "detail": f"{a['hits']} of {a['n']} questions", "used": name == "hybrid+rerank"})
        adf = pd.DataFrame(rows)
        ours = adf.loc[adf["used"], "found"].max() if adf["used"].any() else None
        best = adf["found"].max()
        if ours is not None and ours >= best and (adf["found"] < ours).any():
            verdict = ("Combining keyword and meaning search, then re-ranking, finds it most often"
                       + (" (tied with another method on this set)" if (adf.loc[~adf["used"], "found"] == ours).any() else "")
                       + ". That is what FilingLens uses.")
        else:
            verdict = "FilingLens combines keyword and meaning search, then re-ranks; the bars show how each method does."
        st.markdown(f'<p class="fl-muted">An answer is only as good as the page it is built on. We tried four ways of '
                    f'searching the filings. {verdict}</p>', unsafe_allow_html=True)
        st.altair_chart(
            alt.Chart(adf).mark_bar(cornerRadiusEnd=4, size=20).encode(
                x=alt.X("found:Q", title="Right page found in the top 8 (%)", scale=alt.Scale(domain=[0, 100])),
                y=alt.Y("method:N", sort=list(adf["method"]), title=None,
                        axis=alt.Axis(labelLimit=280, ticks=False, domain=False)),
                opacity=alt.condition("datum.used", alt.value(1), alt.value(0.45)),
                tooltip=[alt.Tooltip("method:N", title="Method"), alt.Tooltip("found:Q", title="Found (%)"),
                         alt.Tooltip("detail:N", title="Questions")],
            ).properties(height=alt.Step(40)), width="stretch")

    with st.expander("Technical details"):
        if abl:
            st.markdown("**Retrieval ablation** (hit@8 = a gold page is among the 8 excerpts the model reads; "
                        "MRR = mean reciprocal rank of the first gold excerpt; cross-doc = every quarter's filing retrieved)")
            st.dataframe(pd.DataFrame([{
                "Config": k, "MRR": f"{v['all']['mrr']:.2f}" if v.get("all") else "—",
                "Hit@8": cell({"k": v["all"]["hits"], "n": v["all"]["n"]}) if v.get("all") else "—",
                "Cross-doc": cell({"k": v["cross_doc"]["all_gold"], "n": v["cross_doc"]["n"]}) if "cross_doc" in v else "—"}
                for k, v in abl.items()]), hide_index=True, width="stretch")
        if e2e:
            st.markdown("**End to end, by configuration**")
            st.dataframe(pd.DataFrame([{"Config": k, "Accuracy": cell(m["accuracy"]),
                                        "Citation precision": cell(m["citation_precision"]),
                                        "Precision without verifier": cell(m["citation_precision_no_verifier"]),
                                        "Verifier rejected": cell(m["verifier_rejection"]),
                                        "Correct refusals": cell(m["abstention"]), "False refusals": cell(m["false_abstention"]),
                                        "Median seconds": m["median_latency_s"]} for k, m in e2e.items()]),
                         hide_index=True, width="stretch")
        st.caption("Cells over fewer than 5 questions show counts, not percentages. The judge model differs from every "
                   "answering model. Reproduce: `python -m eval.run_eval --configs hybrid+rerank,hybrid,"
                   "hybrid+rerank@flash,hybrid+rerank@weak,hybrid+rerank@v2`")
    next_step(HOME, "Home →", "Back to the overview.")


LAND = st.Page(landing, title="FilingLens", icon="🔎", default=True)
HOME = st.Page(home, title="Home", icon="🏠", url_path="home")
BRIEF = st.Page(brief_page, title="Brief", icon="📋", url_path="brief")
DASH = st.Page(dashboard, title="Dashboard", icon="📊", url_path="dashboard")
ASK = st.Page(ask_page, title="Ask", icon="💬", url_path="ask")
EVAL = st.Page(evaluation, title="Evaluation", icon="📏", url_path="evaluation")
FLAG = st.Page(flag_page, title="Red flag", icon="🚩", url_path="flag")
NAV = [(HOME, "Home", "🏠", "Overview and a live example"),
       (BRIEF, "Brief", "📋", "A cited summary of any company"),
       (DASH, "Dashboard", "📊", "Red flags for all 44 stocks"),
       (ASK, "Ask", "💬", "Question any company's filings"),
       (EVAL, "Evaluation", "📏", "How accurate it is")]
current = st.navigation([LAND] + [n[0] for n in NAV] + [FLAG], position="hidden")
if current is not LAND:
    with st.sidebar:
        st.markdown('<div class="fl-brand"><div class="mark">FL</div><div class="name">FilingLens</div></div>'
                    '<p class="fl-brand-sub">Answers from company filings, proven on the page.</p>', unsafe_allow_html=True)
        for pg, title, icon, blurb in NAV:
            st.page_link(pg, label=title, icon=icon, width="stretch")
            st.markdown(f'<div class="fl-navcap">{blurb}</div>', unsafe_allow_html=True)
        st.divider()
        if st.session_state.get("symbol"):
            st.caption(f"Current stock: **{st.session_state['symbol']}**")
        st.caption("Research tool. Not investment advice.")
current.run()
