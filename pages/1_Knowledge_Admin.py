"""Support Ops knowledge admin (FR-37, FR-38, FR-39): edit FAQ, upload guides, review tickets, re-index."""
import csv
import io
import os
import sqlite3

import pandas as pd
import streamlit as st

import config
import ingest_faq
import ingest_guides
import ingest_tickets
import interaction_log

st.set_page_config(page_title="Knowledge Admin", page_icon="🗂️", layout="wide")
st.title("🗂️ Knowledge Admin")
st.caption("Changes go live as soon as you re-index. No code release needed.")

passcode = os.getenv("ADMIN_PASSCODE", "")
if passcode and st.text_input("Admin passcode", type="password") != passcode:
    st.stop()


def reindex(name: str, fn) -> None:
    with st.spinner(f"Re-indexing {name}…"):
        count = fn()
    st.cache_resource.clear()  # chat page picks up the new index
    interaction_log.write("reindex", {"collection": name, "documents": count})
    st.success(f"{name}: {count} documents indexed and live.")


faq_tab, guide_tab, ticket_tab = st.tabs(["FAQ", "PDF guides", "Resolved tickets"])

with faq_tab:
    df = pd.read_csv(config.FAQ_CSV, dtype=str).fillna("")
    st.markdown("Edit entries in place, add rows at the bottom, or delete rows to retire them.")
    edited = st.data_editor(df, num_rows="dynamic", use_container_width=True, hide_index=True,
                            column_config={"answer": st.column_config.TextColumn(width="large")})
    c1, c2, c3 = st.columns(3)
    if c1.button("Save & re-index FAQ", type="primary"):
        edited = edited[(edited["question"].str.strip() != "") & (edited["answer"].str.strip() != "")].copy()
        next_id = pd.to_numeric(edited["id"], errors="coerce").max()
        next_id = int(next_id) + 1 if pd.notna(next_id) else 1
        for i in edited.index[edited["id"].str.strip() == ""]:
            edited.at[i, "id"] = str(next_id)
            next_id += 1
        edited.to_csv(config.FAQ_CSV, index=False, quoting=csv.QUOTE_MINIMAL)
        reindex("faq", ingest_faq.ingest)
    c2.download_button("Export FAQ CSV", df.to_csv(index=False), "faq.csv", "text/csv")
    upload = c3.file_uploader("Import FAQ CSV", type="csv", label_visibility="collapsed")
    if upload is not None and st.button("Replace FAQ with uploaded CSV"):
        new = pd.read_csv(io.BytesIO(upload.getvalue()), dtype=str)
        if not {"id", "question", "answer", "category"} <= set(new.columns):
            st.error("CSV must have columns: id, question, answer, category")
        else:
            new.to_csv(config.FAQ_CSV, index=False)
            reindex("faq", ingest_faq.ingest)

with guide_tab:
    pdfs = sorted(config.GUIDES_DIR.glob("*.pdf"))
    st.markdown("**Published guides:** " + (", ".join(p.name for p in pdfs) or "none"))
    new_pdf = st.file_uploader("Upload or replace a PDF guide", type="pdf")
    if new_pdf is not None and st.button("Publish guide & re-index", type="primary"):
        (config.GUIDES_DIR / new_pdf.name).write_bytes(new_pdf.getvalue())
        reindex("guides", ingest_guides.ingest)
    retire = st.selectbox("Retire a guide", [""] + [p.name for p in pdfs])
    if retire and st.button(f"Retire {retire}"):
        (config.GUIDES_DIR / retire).rename(config.GUIDES_DIR / f"{retire}.retired")
        reindex("guides", ingest_guides.ingest)

with ticket_tab:
    conn = sqlite3.connect(config.TICKETS_DB)
    tickets = pd.read_sql("SELECT ticket_id, category, issue_type, description, resolution, status FROM tickets", conn)
    st.markdown("Only tickets with status **resolved** are indexed. Set a ticket to **excluded** to keep it out "
                "of the bot's answers.")
    edited_t = st.data_editor(
        tickets, use_container_width=True, hide_index=True, disabled=["ticket_id"],
        column_config={"status": st.column_config.SelectboxColumn(
            options=["resolved", "escalated", "open", "excluded"])},
    )
    if st.button("Save & re-index tickets", type="primary"):
        conn.executemany(
            "UPDATE tickets SET category=?, issue_type=?, description=?, resolution=?, status=? WHERE ticket_id=?",
            [(r.category, r.issue_type, r.description, r.resolution, r.status, r.ticket_id)
             for r in edited_t.itertuples()],
        )
        conn.commit()
        reindex("tickets", ingest_tickets.ingest)
    conn.close()
