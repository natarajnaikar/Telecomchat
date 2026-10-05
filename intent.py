"""Intent detection (agent_PRD.md §6.1).

requirement_inquiry -> the existing RAG pipeline; refund_request -> the Refund Agent.
Messages with no refund-like words skip the LLM and go straight to RAG, so ordinary questions
cost no extra call. The rest are classified by the existing LLM (FR-04), with keyword rules as
the fallback if the LLM is unavailable or its reply can't be parsed.
"""
import re
from dataclasses import dataclass

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

REQUIREMENT_INQUIRY = "requirement_inquiry"
REFUND_REQUEST = "refund_request"

# Anything that might be a refund request. Broad on purpose: it only decides whether to ask the LLM.
_REFUND_HINT = re.compile(r"refund|money back|revers|cancel|recharge|return|charged|₹|\brs\.?\s?\d|\binr\b|\bback\b",
                          re.I)
# Fallback rules: an action word, not phrased as a how-to or policy question (FR-05a).
_REFUND_ACTION = re.compile(r"\b(refund|money back|reverse|cancel)\b", re.I)
_HOW_TO = re.compile(r"^\s*(how|what|when|where|why|which|is there|are there|does|do you)\b|\bpolicy\b", re.I)

PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "Classify the customer's latest message for a mobile operator's support chat.\n\n"
     "refund_request: the customer wants money back for a recharge, or wants a recharge cancelled or "
     "reversed. Example: \"I want a refund of ₹999.\", \"Cancel my last recharge\", \"refund it\".\n"
     "requirement_inquiry: any question or support issue, including questions about HOW refunds or "
     "cancellation work. Example: \"Why is my internet slow?\", \"How do I cancel a recharge?\".\n\n"
     "Use the conversation only to resolve references like \"it\" or \"that one\".\n"
     'Reply with JSON only: {{"intent": "refund_request"}} or {{"intent": "requirement_inquiry"}}'),
    ("human", "Conversation:\n{history}\n\nLatest message: {question}"),
])


@dataclass
class IntentResult:
    intent: str  # REQUIREMENT_INQUIRY | REFUND_REQUEST  (FR-05)
    method: str  # prefilter | llm | rules


def rule_intent(question: str) -> str:
    if _REFUND_ACTION.search(question) and not _HOW_TO.search(question):
        return REFUND_REQUEST
    return REQUIREMENT_INQUIRY


def classify(question: str, history: list[dict], get_llm) -> IntentResult:
    """`get_llm` returns the shared chat model, or raises if it isn't configured."""
    if not _REFUND_HINT.search(question):
        return IntentResult(REQUIREMENT_INQUIRY, "prefilter")
    recent = history[-4:]
    transcript = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in recent) or "(none)"
    try:
        out = (PROMPT | get_llm() | StrOutputParser()).invoke({"history": transcript, "question": question})
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
        found = re.findall(rf"{REFUND_REQUEST}|{REQUIREMENT_INQUIRY}", out)
        if len(set(found)) == 1:
            return IntentResult(found[0], "llm")
    except Exception:
        pass
    return IntentResult(rule_intent(question), "rules")

