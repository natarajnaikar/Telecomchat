"""Support Ops analytics (FR-44, FR-45): containment, escalations, feedback, content gaps."""
import pandas as pd
import streamlit as st

import interaction_log

st.set_page_config(page_title="Assistant Analytics", page_icon="📊", layout="wide")
st.title("📊 Assistant Analytics")

events = interaction_log.read_all()
turns = pd.DataFrame([e for e in events if e["event"] == "turn"])
feedback = pd.DataFrame([e for e in events if e["event"] == "feedback"])
escalations = pd.DataFrame([e for e in events if e["event"] == "escalation"])

if turns.empty:
    st.info("No conversations logged yet. Ask the assistant a few questions first.")
    st.stop()

sessions = turns["session_id"].nunique()
escalated_sessions = escalations["session_id"].nunique() if not escalations.empty else 0
down_sessions = set(feedback.loc[feedback["rating"] == "down", "session_id"]) if not feedback.empty else set()
contained = len(set(turns["session_id"]) - set(escalations.get("session_id", [])) - down_sessions)
rated = feedback.drop_duplicates(["session_id", "question"], keep="last") if not feedback.empty else feedback

c = st.columns(5)
c[0].metric("Conversations", sessions)
c[1].metric("Containment", f"{contained / sessions:.0%}", help="No escalation and no 👎")
c[2].metric("Escalation rate", f"{escalated_sessions / sessions:.0%}")
c[3].metric("👍 rate", f"{(rated['rating'] == 'up').mean():.0%}" if not rated.empty else "–")
c[4].metric("p95 latency", f"{turns['latency_s'].quantile(0.95):.1f}s",
            help="Time to prepare the response (streamed answers log on completion)")

left, right = st.columns(2)
with left:
    st.subheader("Answer outcomes")
    st.bar_chart(turns["kind"].value_counts())
with right:
    st.subheader("Escalations by trigger")
    if escalations.empty:
        st.caption("None yet.")
    else:
        st.bar_chart(escalations.explode("triggers")["triggers"].value_counts())

st.subheader("Content gaps: questions the assistant couldn't answer or got 👎")
gaps = turns.loc[turns["kind"].isin(["no_answer", "blocked"]), ["ts", "question", "best_score", "kind"]]
if not feedback.empty:
    downs = feedback.loc[feedback["rating"] == "down", ["ts", "question", "reason"]].assign(kind="👎")
    gaps = pd.concat([gaps, downs], ignore_index=True)
if gaps.empty:
    st.caption("No gaps recorded.")
else:
    st.dataframe(gaps.sort_values("ts", ascending=False), use_container_width=True, hide_index=True)
    st.download_button("Export content gaps (CSV)", gaps.to_csv(index=False), "content_gaps.csv", "text/csv")

with st.expander("Recent turns (redacted trace)"):
    cols = ["ts", "question", "rewritten_query", "kind", "flags", "triggers", "best_score", "latency_s"]
    st.dataframe(turns[cols].sort_values("ts", ascending=False).head(200), use_container_width=True, hide_index=True)
