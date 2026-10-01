"""Human handoff (FR-31..FR-36).

v1 has no live agent platform wired in (PRD open question Q2), so a confirmed handoff is
written to logs/handoffs.jsonl as the package an agent would receive.
"""
import uuid
from datetime import datetime

import config
import interaction_log

TRIGGER_LABELS = {
    "E1_human_request": "Customer asked for a person",
    "E2_low_confidence": "Bot couldn't answer twice in a row",
    "E3_feedback_unsolved": "Customer said the answer didn't solve it",
    "E4_high_risk": "High-risk topic (fraud, disputes, unauthorised changes, refunds)",
    "E5_frustration": "Customer frustration",
    "E6_safety": "Safety / emergency signal",
    "E7_personal_data": "Needs account-specific help",
    "manual": "Customer used 'Talk to a person'",
}


def is_staffed(now: datetime | None = None) -> bool:
    start, end = config.LIVE_CHAT_HOURS
    return start <= (now or datetime.now()).hour < end


def channel_message(now: datetime | None = None) -> str:
    start, end = config.LIVE_CHAT_HOURS
    if is_staffed(now):
        return (f"Our live chat team is online now ({start}am–{end - 12}pm). I can pass this conversation "
                f"to an agent so you won't need to repeat yourself. You can also call "
                f"**{config.SUPPORT_PHONE}** (free from your mobile).")
    return (f"Our live chat team is offline right now (hours: {start}am–{end - 12}pm). I can request a "
            f"callback and pass on this conversation, or you can call **{config.SUPPORT_PHONE}** "
            f"(free from your mobile).")


def create_handoff(session_id: str, transcript: list[dict], triggers: list[str], topic: str,
                   sources: list[str], feedback: list[dict], contact: dict | None) -> str:
    """Record the handoff package and return a reference the customer can quote."""
    ref = "NC-" + uuid.uuid4().hex[:8].upper()
    mode = "live_chat" if is_staffed() and not contact else "callback"
    # Transcript is redacted; contact details are kept only in the handoff record,
    # and only after the customer consented by submitting them (FR-34).
    interaction_log.write("handoff", {
        "reference": ref,
        "session_id": session_id,
        "mode": mode,
        "triggers": triggers,
        "topic": topic,
        "sources_tried": sources,
        "feedback": feedback,
        "transcript": [f"{m['role']}: {m['content']}" for m in transcript],
    }, path=config.HANDOFF_LOG)
    if contact:
        interaction_log.write("handoff_contact", {"reference": ref, **contact},
                              path=config.HANDOFF_LOG, redact=False)
    interaction_log.write("escalation", {"session_id": session_id, "reference": ref,
                                         "mode": mode, "triggers": triggers, "topic": topic})
    return ref
