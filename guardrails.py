"""Rule-based guardrails: intent flags, PII redaction, and the numeric grounding check.

v1 uses transparent keyword/regex rules so Support Ops and Compliance can audit them.
"""
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# PII redaction (FR-30, NFR-13, NFR-14)
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{8,}\d)(?!\w)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("SECRET", re.compile(r"\b(pin|password|passcode|puk|cvv)\b(\s*(is|:|=)?\s*)\S+", re.I)),
]


def redact_pii(text: str) -> str:
    for label, pattern in _PII_PATTERNS:
        if label == "SECRET":
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub(f"[{label}]", text)
    return text


def contains_sensitive_data(text: str) -> bool:
    return redact_pii(text) != text


# ---------------------------------------------------------------------------
# Intent flags (FR-25..FR-28, escalation triggers E1, E4, E5, E6)
# ---------------------------------------------------------------------------
def _rx(*phrases: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(phrases) + r")\b", re.I)


HUMAN_REQUEST = _rx(
    r"(talk|speak|chat) (to|with) (a |an |some)?(human|person|agent|someone|representative|rep)",
    r"real (person|human)", r"human agent", r"live (agent|chat|person)", r"customer service rep\w*",
    r"transfer me", r"connect me", r"escalate",
)
HIGH_RISK = _rx(
    r"fraud\w*", r"scam\w*", r"hack\w*", r"compromis\w*", r"stolen identity",
    r"without (my )?(permission|consent|authori[sz]ation|asking)", r"unauthori[sz]ed",
    r"sim swap", r"someone (else )?(changed|ported|took|accessed)",
    r"dispute", r"refund", r"chargeback", r"money back", r"compensation",
    r"lawyer", r"legal action", r"sue", r"regulator", r"ombudsman", r"formal complaint",
    r"cancel (my )?(contract|account|service)", r"switch(ing)? (to )?another (provider|network|carrier)",
)
EMERGENCY = _rx(
    r"emergency", r"911", r"999", r"112", r"ambulance", r"can'?t call (the )?(police|ambulance)",
    r"suicid\w*", r"self[- ]harm", r"in danger", r"being abused",
)
PERSONAL_DATA = re.compile(
    r"\b(my|mine|i have|i'?ve got|how much (data|credit|balance) (do )?i)\b.{0,40}?"
    r"\b(balance|usage|used|remaining|left|credit|bill (amount|total)|how much (do|did) i owe|"
    r"current plan|which plan|what plan|payment status|port(ing)? status|order status|last bill|"
    r"due date|contract end)\b"
    r"|\bwhy (was|am|did) i (charged|billed)\b"
    r"|\bhow much (data|credit|balance|money) (do i have|is left|have i)\b",
    re.I,
)
INJECTION = _rx(
    r"ignore (all |your |the |previous |prior )*(rules|instructions|prompt)",
    r"disregard (all |your |the )*(rules|instructions)",
    r"(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions)",
    r"you are now", r"pretend (to be|you are)", r"role-?play", r"jailbreak",
    r"developer mode", r"free month", r"give me (a )?(free|discount|credit)",
)
FRUSTRATION = _rx(
    r"useless", r"not helpful", r"doesn'?t help", r"didn'?t (help|work)", r"still (not|doesn'?t|isn'?t)",
    r"ridiculous", r"terrible", r"awful", r"angry", r"frustrat\w*", r"fed up", r"waste of time", r"stupid",
)


@dataclass
class IntentFlags:
    human_request: bool = False
    high_risk: bool = False
    emergency: bool = False
    personal_data: bool = False
    injection: bool = False
    frustration: bool = False
    sensitive_data_shared: bool = False

    def active(self) -> list[str]:
        return [k for k, v in self.__dict__.items() if v]


def detect_intents(text: str) -> IntentFlags:
    return IntentFlags(
        human_request=bool(HUMAN_REQUEST.search(text)),
        high_risk=bool(HIGH_RISK.search(text)),
        emergency=bool(EMERGENCY.search(text)),
        personal_data=bool(PERSONAL_DATA.search(text)),
        injection=bool(INJECTION.search(text)),
        frustration=bool(FRUSTRATION.search(text)),
        sensitive_data_shared=contains_sensitive_data(text),
    )


# ---------------------------------------------------------------------------
# Numeric guardrail (FR-21): every number in the answer must appear in the context.
# ---------------------------------------------------------------------------
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_MONEY = re.compile(r"[$£€]\s?(\d+(?:[.,]\d+)*)")
_CITATION = re.compile(r"\[[^\]]*\]|\b(?:FAQ\s*#\s*\d+|TK-\d+|p\.\s*\d+|§\s*\d+)", re.I)
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|step\s+\d+[:.)-]?)\s", re.I | re.M)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def unsupported_numbers(answer: str, context: str, question: str = "") -> list[str]:
    """Numbers in the answer that the context does not support.

    Prices must match a price in the context itself. Other numbers (e.g. a phone model the
    customer mentioned) may also come from the customer's question.
    """
    cleaned = _LIST_MARKER.sub(" ", _CITATION.sub(" ", answer))
    in_context = _numbers(context)
    context_prices = {n.replace(",", "") for n in _MONEY.findall(context)}
    money = {n.replace(",", "") for n in _MONEY.findall(cleaned)}
    allowed = in_context | _numbers(question)
    bad = {n for n in money if n not in context_prices}
    bad |= {n for n in _numbers(cleaned) if n not in allowed}
    return sorted(bad)
