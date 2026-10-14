"""Streamlit UI for the Data Catalog Search Assistant.

Run:  streamlit run app/streamlit_app.py
(Build the catalog first: python -m catalog_assistant setup)
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from catalog_assistant import db  # noqa: E402
from catalog_assistant.assistant import CatalogAssistant  # noqa: E402
from catalog_assistant.config import Settings  # noqa: E402
from catalog_assistant.llm import LLMError, create_client  # noqa: E402
from catalog_assistant.search import InvalidQueryError, list_roles, visible_tables  # noqa: E402

EXAMPLES = [
    "Which table has customer attrition numbers?",
    "employee salaries by department",
    "customer email addresses",
    "unpaid invoices and their due dates",
    "weather forecast for tomorrow",
]
STATUS_COLOR = {"answered": "green", "low_confidence": "orange", "insufficient_evidence": "orange",
                "no_match": "red"}

st.set_page_config(page_title="Data Catalog Search", layout="wide")
settings = Settings.from_env()
if not settings.catalog_db.exists():
    st.error("Catalog not built yet. Run `python -m catalog_assistant setup` and reload.")
    st.stop()
con = db.connect(settings.catalog_db)

# ------------------------------------------------------------------ sidebar ---
roles = list_roles(con)
with st.sidebar:
    st.header("Who is asking?")
    role = st.selectbox("User role", [r["user_role"] for r in roles], index=1, key="role")
    st.caption("Grants: " + next(r["grants"] for r in roles if r["user_role"] == role))
    provider = st.selectbox("AI provider", ["offline", "ollama", "openai_compatible"],
                            index=["offline", "ollama", "openai_compatible"].index(
                                settings.llm_provider if settings.llm_provider in
                                ("offline", "ollama", "openai_compatible") else "offline"), key="provider")
    st.caption("offline = deterministic retrieval-only answers; no model calls.")

st.title("Data Catalog Search Assistant")
tab_ask, tab_browse, tab_pipeline = st.tabs(["Ask", "Browse catalog", "Pipeline & quality"])

# ---------------------------------------------------------------------- ask ---
with tab_ask:
    example = st.selectbox("Example questions", ["(type your own)"] + EXAMPLES, key="example")
    question = st.text_input("What data are you looking for?",
                             value="" if example == "(type your own)" else example)
    if st.button("Search", type="primary", key="search") and question:
        try:
            assistant = CatalogAssistant(con, create_client(settings, provider))
            a = assistant.ask(question, role)
        except (InvalidQueryError, ValueError, LLMError) as exc:
            st.error(str(exc))
            st.stop()

        color = STATUS_COLOR.get(a.status, "gray")
        st.markdown(f":{color}[**{a.status.replace('_', ' ')}**] · confidence **{a.confidence}** · "
                    f"provider `{a.provider}` · AI validation `{a.ai_validation}` · {a.latency_ms} ms")
        st.info(a.answer)
        for i, r in enumerate(a.recommendations, 1):
            badge = " ✔ certified" if r.certified else ""
            with st.expander(f"{i}. {r.table_key}  —  access: {r.access_role}{badge}", expanded=i == 1):
                st.write(r.description or "_No description yet._")
                st.caption(f"Owner: {r.owner or '-'} · matched terms: {', '.join(r.matched_terms)} · "
                           f"retrieval confidence: {r.confidence} · source: {r.source}")
                st.write(f"**Why:** {r.reason}")
                if r.columns:
                    st.dataframe(r.columns, width="stretch", hide_index=True)
                if r.hidden_column_count:
                    st.caption(f"{r.hidden_column_count} column(s) hidden by your role.")
        for n in a.notes:
            st.warning(n)
        with st.expander("Search terms used"):
            st.write(a.search_terms)

# ------------------------------------------------------------------- browse ---
with tab_browse:
    st.subheader(f"Tables visible to `{role}`")
    st.dataframe(visible_tables(con, role), width="stretch", hide_index=True)

# ----------------------------------------------------------------- pipeline ---
with tab_pipeline:
    st.subheader("Pipeline runs")
    st.dataframe([dict(r) for r in con.execute("SELECT * FROM v_run_summary ORDER BY run_id DESC")],
                 width="stretch", hide_index=True)
    last = con.execute("SELECT MAX(run_id) FROM etl_run WHERE status = 'success'").fetchone()[0]
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"Change log (run {last})")
        st.dataframe([dict(r) for r in con.execute(
            "SELECT entity_type, entity_key, change_type, changed_fields FROM change_log WHERE run_id = ?",
            (last,))], width="stretch", hide_index=True)
    with c2:
        st.subheader(f"Quarantine (run {last})")
        st.dataframe([dict(r) for r in con.execute(
            "SELECT entity, source_row, record_key, reason FROM quarantine WHERE run_id = ?", (last,))],
            width="stretch", hide_index=True)
    st.subheader("Metadata quality")
    st.dataframe([dict(r) for r in con.execute("SELECT * FROM v_metadata_quality ORDER BY documented_pct")],
                 width="stretch", hide_index=True)
