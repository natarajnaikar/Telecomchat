"""Streamlit chat UI for the NovaCell Support Assistant.

Run:  streamlit run app.py
"""
import uuid

import streamlit as st

import config
import escalation
from assistant import ConversationState, SupportAssistant, log_feedback

st.set_page_config(page_title="NovaCell Support", page_icon="💬", layout="centered")

SAMPLE_QUESTIONS = [
    "Why is my mobile internet so slow?",
    "Why is my bill higher than usual?",
    "My eSIM won't activate. What should I do?",
    "How do I activate roaming before I travel?",
    "My calls keep going straight to voicemail",
    "I can't log in to the MyTelecom app",
    "What's my data balance?",
]

WELCOME = (
    "Hi! I'm the NovaCell support assistant. I can help with **mobile data, bills, SIM/eSIM, roaming, "
    "calls and the app**, using NovaCell's official help content.\n\n"
    "I **can't see your account** (balance, usage or bills), and I won't ask for passwords or card "
    "details. You can talk to a person at any time using the button in the sidebar."
)
DOWN_REASONS = ["Wrong information", "Unclear", "Didn't solve my problem", "Other"]


@st.cache_resource(show_spinner="Loading knowledge base…")
def get_assistant() -> SupportAssistant:
    return SupportAssistant()


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("messages", [])
    ss.setdefault("conv", ConversationState())
    ss.setdefault("pending", None)
    ss.setdefault("escalation", None)  # None | {"triggers": [...]} | {"reference": ...}
    ss.setdefault("feedback_logged", {})


def reset() -> None:
    for key in ["messages", "conv", "pending", "escalation", "feedback_logged"]:
        st.session_state.pop(key, None)
    init_state()


def history() -> list[dict]:
    return [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]


def open_escalation(triggers: list[str]) -> None:
    current = st.session_state.escalation
    if current and "reference" in current:
        return
    st.session_state.escalation = {"triggers": sorted(set((current or {}).get("triggers", []) + triggers))}


# ---------------------------------------------------------------------------
def render_sources(msg: dict) -> None:
    if not msg.get("sources"):
        return
    with st.expander(f"Sources ({len(msg['sources'])})"):
        for s in msg["sources"]:
            label = {"FAQ": "FAQ", "GUIDES": "GUIDE", "TICKETS": "RESOLVED CASE"}[s["label"]]
            st.markdown(f"**{label} · {s['doc_id']}** — relevance {s['score']:.2f}")
            st.caption(s["content"][:500])


def render_feedback(idx: int, msg: dict) -> None:
    if msg.get("kind") not in {"answer", "boundary", "no_answer", "blocked"}:
        return
    key = msg["id"]
    rating = st.feedback("thumbs", key=f"fb_{key}")
    if rating is None:
        return
    logged = st.session_state.feedback_logged
    question = st.session_state.messages[idx - 1]["content"] if idx > 0 else ""
    if rating == 1 and logged.get(key) != "up":
        log_feedback(st.session_state.conv.session_id, question, "up")
        logged[key] = "up"
    elif rating == 0:
        if logged.get(key) is None:
            log_feedback(st.session_state.conv.session_id, question, "down")
            logged[key] = "down"
        reason = st.radio("What went wrong? (optional)", DOWN_REASONS, index=None,
                          key=f"why_{key}", horizontal=True)
        if reason and logged.get(key) == "down":
            log_feedback(st.session_state.conv.session_id, question, "down", reason=reason)
            logged[key] = f"down:{reason}"
            if reason == "Didn't solve my problem":
                open_escalation(["E3_feedback_unsolved"])
                st.rerun()


def render_escalation() -> None:
    esc = st.session_state.escalation
    if not esc:
        return
    with st.container(border=True):
        if "reference" in esc:
            if esc["mode"] == "callback":
                st.success(f"Callback requested. Your reference is **{esc['reference']}**. "
                           f"Our team will call you with this conversation in hand.")
            else:
                st.success(f"You're in the queue for a live agent. Reference **{esc['reference']}**. "
                           f"They'll see this conversation, so you won't need to repeat yourself.")
            return

        st.markdown("#### Talk to a person")
        reasons = [escalation.TRIGGER_LABELS.get(t, t) for t in esc["triggers"]]
        if reasons:
            st.caption("Why I'm suggesting this: " + "; ".join(reasons))
        st.markdown(escalation.channel_message())

        staffed = escalation.is_staffed()
        cols = st.columns(2)
        if staffed and cols[0].button("Connect me to an agent", type="primary"):
            finish_handoff(contact=None)
        if cols[1 if staffed else 0].button("No thanks, keep chatting"):
            st.session_state.escalation = None
            st.rerun()

        with st.form("callback"):
            st.markdown("**Request a callback**" + (" instead" if staffed else ""))
            name = st.text_input("Your name")
            phone = st.text_input("Phone number to call you on")
            consent = st.checkbox("I agree to NovaCell contacting me and sharing this chat with an agent.")
            if st.form_submit_button("Request callback"):
                if not (name.strip() and phone.strip() and consent):
                    st.warning("Please enter your name and phone number and tick the consent box.")
                else:
                    finish_handoff(contact={"name": name.strip(), "phone": phone.strip()})


def finish_handoff(contact: dict | None) -> None:
    ss = st.session_state
    triggers = ss.escalation.get("triggers") or ["manual"]
    last_bot = next((m for m in reversed(ss.messages) if m["role"] == "assistant"), {})
    sources = sorted({s["doc_id"] for m in ss.messages for s in m.get("sources", [])})
    feedback = [{"message": k, "rating": v} for k, v in ss.feedback_logged.items()]
    ref = escalation.create_handoff(ss.conv.session_id, history(), triggers,
                                    topic=last_bot.get("topic", ""), sources=sources,
                                    feedback=feedback, contact=contact)
    ss.escalation = {"reference": ref, "mode": "callback" if contact else "live_chat"}
    st.rerun()


# ---------------------------------------------------------------------------
def answer(question: str) -> None:
    assistant = get_assistant()
    ss = st.session_state
    prior = history()
    ss.messages.append({"role": "user", "content": question, "id": uuid.uuid4().hex})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Searching NovaCell help content…"):
            try:
                turn = assistant.respond(question, prior, ss.conv)
            except RuntimeError as exc:  # missing API key
                st.error(str(exc))
                ss.messages.pop()
                return
        if turn.notice:
            st.warning(turn.notice)
        box = st.empty()
        shown = ""
        for chunk in turn.stream():
            shown += chunk
            box.markdown(shown + "▌")
        box.markdown(turn.text)

    categories = {s.label for s in turn.cited_sources}
    msg = {
        "role": "assistant", "content": turn.text, "id": uuid.uuid4().hex, "kind": turn.kind,
        "notice": turn.notice,
        "sources": [s.as_dict() for s in turn.cited_sources] if turn.kind in {"answer", "boundary"} else [],
        "topic": ",".join(sorted(categories)),
    }
    ss.messages.append(msg)
    if turn.triggers:
        open_escalation(turn.triggers)
    st.rerun()


# ---------------------------------------------------------------------------
init_state()

with st.sidebar:
    st.title("💬 NovaCell Support")
    st.caption("AI assistant · answers come only from NovaCell's FAQ, resolved cases and guides.")
    if st.button("🙋 Talk to a person", use_container_width=True, type="primary"):
        open_escalation(["manual"])
    if st.button("🗑️ Clear conversation", use_container_width=True):
        reset()
        st.rerun()
    st.divider()
    st.subheader("Try asking")
    for q in SAMPLE_QUESTIONS:
        if st.button(q, key=f"sample_{q}", use_container_width=True):
            st.session_state.pending = q
    st.divider()
    st.caption("Please don't share passwords, PINs or card numbers. Chats are stored for 90 days with "
               "personal details removed, to improve the service.")
    if not config.GROQ_API_KEY:
        st.error("GROQ_API_KEY missing: add it to `.env` and restart.")

st.header("How can we help today?")
with st.chat_message("assistant"):
    st.markdown(WELCOME)

for i, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"]):
        if m.get("notice"):
            st.warning(m["notice"])
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_sources(m)
            render_feedback(i, m)

render_escalation()

typed = st.chat_input("Ask about data, bills, SIM, roaming, calls…", max_chars=config.MAX_INPUT_CHARS)
question = typed or st.session_state.pop("pending", None)
if question:
    answer(question)
