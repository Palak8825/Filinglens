import os
from urllib.parse import quote

import requests
import streamlit as st

API = os.environ.get("FILINGLENS_API", "http://localhost:8000")

st.set_page_config(page_title="FilingLens", layout="wide")
st.title("FilingLens")
st.caption("Ask the filings. Every claim links to the page. Research tool, not advice.")


@st.cache_data(ttl=300)
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


def show_page(url: str | None, caption: str | None = None) -> None:
    """Render a cited page; if its PDF isn't in this download, say so instead of a broken image."""
    if not url:
        return
    png = fetch_png(url)
    if png:
        st.image(png, caption=caption, width="stretch")
    else:
        st.caption("Page image not in this download (the quote was still verified against the indexed text).")


def page_url(document_id: int | None, page: int | None, path: str | None = None) -> str | None:
    if document_id and page:
        return f"{API}/doc/{document_id}/page/{page}.png"
    if path and page:
        return f"{API}/evidence/page.png?path={quote(path)}&page={page}"
    return None


try:
    symbols = get_json("/symbols")
except requests.RequestException as e:
    st.error(f"API not reachable at {API} — start it with `uvicorn api.main:app`. ({e})")
    st.stop()
if not symbols:
    st.warning("Index is empty. Run `python -m ingest.run` first.")
    st.stop()

status = get_json("/status")
if not status["has_api_key"]:
    st.info(f"No LLM API key set. Red flags, the risk screen, page images and the selftest all work; "
            f"Q&A answers the {status['cached_answers']} cached example questions below (marked *cached*). "
            "Add `LLM_API_KEY` to `.env` and restart the API to ask your own.")

tab_ask, tab_screen = st.tabs(["Ask a stock", "Risk screen (all symbols)"])

with tab_ask:
    col_q, col_f = st.columns([3, 2])

    with col_q:
        # Flags work for every symbol with facts; Q&A only where filings are indexed.
        all_syms = sorted(set(symbols) | {r["symbol"] for r in get_json("/screen")})
        symbol = st.selectbox("Stock", all_syms, index=all_syms.index("YESBANK") if "YESBANK" in all_syms else 0,
                              format_func=lambda s: s if s in symbols else f"{s}  (flags only)")
        indexed = symbol in symbols
        if not indexed:
            st.info(f"{symbol}'s filings aren't in the Q&A index yet — the red-flag panel still works.")
        examples = get_json(f"/examples?symbol={symbol}") if indexed else []
        if examples:
            st.caption("Example questions (cached answers; they work without an API key):")
            for i, ex in enumerate(examples):
                if st.button(ex["question"], key=f"ex-{symbol}-{i}"):
                    st.session_state["q"] = ex["question"]
                    st.session_state["pending"] = True
        q = st.text_input("Question", key="q", placeholder="What was the net interest margin in the latest quarter and why did it move?",
                          disabled=not indexed)
        c1, c2 = st.columns([2, 1])
        doc_type = c1.selectbox("Restrict to", ["any", "results", "concall", "announcement", "shareholding"],
                                disabled=not indexed)
        live = c2.checkbox("Ask live", value=False, disabled=not (indexed and status["has_api_key"]),
                           help="Skip the cache and send the question to the model now, even if it's an example.")
        clicked = st.button("Ask", type="primary", disabled=not indexed)
        if (clicked or st.session_state.pop("pending", False)) and q:
            with st.spinner("Retrieving, reranking, answering, verifying citations…"):
                resp = requests.post(f"{API}/ask", json={"symbol": symbol, "question": q, "use_cache": not live,
                                                         "doc_type": None if doc_type == "any" else doc_type}, timeout=180)
            if resp.ok:
                st.session_state["last"] = resp.json()
            elif resp.status_code == 503:
                st.info(resp.json().get("detail", "No API key set."))
            else:
                st.error(f"Answer failed ({resp.status_code}): {resp.text[:300]}")

        r = st.session_state.get("last")
        if r and r.get("symbol") == symbol:
            badge = "abstained" if r["abstained"] else f"confidence: {r['confidence']}"
            st.markdown(f"**Answer** · `{badge}` · {len(r['citations'])} verified citation(s)"
                        + (f" · `{r['model']}`" if r.get("model") else ""))
            if r.get("cached"):
                c = r["cached"]
                st.caption(f"*cached* answer, generated {c['generated_on']} with {c['model']} on an index of "
                           f"{c['index_chunks']:,} {symbol} chunks" + (" (index has changed since)" if c["index_changed"] else "")
                           + ". Tick *Ask live* to regenerate.")
            if r.get("period_note"):
                st.caption(r["period_note"])
            st.write(r["answer"])
            if r["dropped_citations"]:
                st.warning(f"{r['dropped_citations']} citation(s) failed verification and were removed.")
            for i, c in enumerate(r["citations"], 1):
                period = f" · {c.get('fiscal_period')}" if c.get("fiscal_period") else ""
                with st.expander(f"[{i}] {c['doc_type']}{period} · {c['title'][:70]} · p.{c['page']}  ✓ verified", expanded=(i == 1)):
                    st.markdown(f"> {c['quote']}")
                    show_page(page_url(c["document_id"], c["page"]))

    with col_f:
        st.subheader("Red flags")
        f = get_json(f"/flags/{symbol}")
        colour = {"low": "green", "elevated": "orange", "high": "red"}[f["level"]]
        st.markdown(f"Risk level: :{colour}[**{f['level'].upper()}**] (score {f['score']})")
        if not f["flags"]:
            st.write("No flags triggered on the available filings.")
        for fl in f["flags"]:
            icon = "🔴" if fl["severity"] == 3 else "🟠"
            with st.expander(f"{icon} {fl['title']}"):
                st.write(fl["detail"])
                structured = [e for e in fl["evidence"] if e.get("values")]
                if structured:  # shareholding / price facts: show the filed numbers side by side
                    st.dataframe([{"period": e.get("period") or e.get("as_of"), **e["values"]} for e in structured],
                                 width="stretch", hide_index=True)
                for e in fl["evidence"]:
                    label = " · ".join(x for x in (e.get("period"), e.get("as_of"), (e.get("title") or "")[:80]) if x)
                    st.caption(label)
                    if e.get("quote"):
                        st.markdown(f"> {e['quote']}")
                    if e.get("source_url"):
                        st.markdown(f"[Source filing]({e['source_url']})")
                    show_page(page_url(e.get("document_id"), e.get("page"), e.get("path")), caption=f"page {e.get('page')}")
        with st.expander("Thresholds"):
            st.json(f["thresholds"])

with tab_screen:
    st.caption("Deterministic rules over shareholding patterns, auditor filings and prices. Click a stock in the "
               "first tab to see the evidence behind each flag.")
    rows = get_json("/screen")
    st.dataframe(
        [{"Symbol": r["symbol"], "Level": r["level"], "Score": r["score"], "Flags": ", ".join(r["flags"]) or "—",
          "Q&A indexed": "yes" if r["symbol"] in symbols else ""} for r in rows],
        width="stretch", hide_index=True,
    )
