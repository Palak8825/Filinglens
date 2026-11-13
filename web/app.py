import os

import requests
import streamlit as st

API = os.environ.get("FILINGLENS_API", "http://localhost:8000")

st.set_page_config(page_title="FilingLens", layout="wide")
st.title("FilingLens")
st.caption("Ask the filings. Every claim links to the page. Research tool, not advice.")

symbols = requests.get(f"{API}/symbols", timeout=30).json() or ["RELIANCE"]
col_q, col_f = st.columns([3, 2])

with col_q:
    symbol = st.selectbox("Stock", symbols)
    q = st.text_input("Question", placeholder="Why did EBITDA margin fall in the latest quarter?")
    doc_type = st.selectbox("Restrict to", ["any", "results", "concall", "annual_report", "shareholding", "announcement"])
    if st.button("Ask", type="primary") and q:
        with st.spinner("Reading filings…"):
            r = requests.post(f"{API}/ask", json={"symbol": symbol, "question": q,
                                                   "doc_type": None if doc_type == "any" else doc_type}, timeout=180).json()
        st.session_state["last"] = r

    r = st.session_state.get("last")
    if r:
        st.markdown(f"**Answer** · confidence: `{r['confidence']}`" + (" · abstained" if r["abstained"] else ""))
        st.write(r["answer"])
        if r["dropped_citations"]:
            st.warning(f"{r['dropped_citations']} citation(s) failed verification and were dropped.")
        for i, c in enumerate(r["citations"], 1):
            with st.expander(f"[{i}] {c['doc_type']} · {c['title'][:70]} · p.{c['page']}  ✓ verified"):
                st.markdown(f"> {c['quote']}")
                st.image(f"{API}/doc/{c['document_id']}/page/{c['page']}.png", use_container_width=True)

with col_f:
    st.subheader("Red flags")
    f = requests.get(f"{API}/flags/{symbol}", timeout=60).json()
    colour = {"low": "green", "elevated": "orange", "high": "red"}[f["level"]]
    st.markdown(f"Risk level: :{colour}[**{f['level'].upper()}**] (score {f['score']})")
    if not f["flags"]:
        st.write("No flags triggered on available filings.")
    for fl in f["flags"]:
        with st.expander(f"{'🔴' if fl['severity']==3 else '🟠'} {fl['title']}"):
            st.write(fl["detail"])
            for e in fl["evidence"]:
                if e.get("document_id") and e.get("page"):
                    st.image(f"{API}/doc/{e['document_id']}/page/{e['page']}.png", caption=e.get("period"), use_container_width=True)
    with st.expander("Thresholds"):
        st.json(f["thresholds"])
