"""Refund Agent (agent_PRD.md §6.2-§6.8, §9, §10).

The LLM is given two read-only tools, get_user_account and validate_refund, and decides how to use
them for the customer's message. Everything after that is application code: the amount must be one
the customer actually stated (FR-44), eligibility comes from validate_refund (FR-30), the ₹499
approval limit decides confirmation vs. Support Team (FR-36a), and only this module calls
process_refund / submit_to_support (FR-23a). Replies are fixed templates filled from tool results,
so the agent can't promise an outcome the tools didn't produce (FR-40a, GR-09, GR-10).
If the LLM is unavailable the same steps run without it.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import config
from intent import REQUIREMENT_INQUIRY
from refund_tools import RefundTools, inr

log = logging.getLogger("refund_agent")

AGENT_PROMPT = f"""You are NovaCell's Refund Agent. The customer is already signed in, so never ask for a
user ID, phone number, password or account details.

Tools:
- get_user_account(): the customer's plan and recent recharges.
- validate_refund(amount): checks a refund amount in rupees against the refund rules.

Steps:
1. Call get_user_account.
2. If the customer's latest message states a refund amount, call validate_refund with exactly that amount.
3. If it doesn't state an amount, do not guess one and do not call validate_refund.

Never decide eligibility yourself and never promise a refund. You cannot process refunds: the
application handles confirmation and execution. Finish with one short sentence."""


class RefundStatus(str, Enum):  # §10 state requirements
    IDLE = "idle"
    REQUESTED = "refund_requested"
    VALIDATED = "refund_validated"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_AMOUNT = "awaiting_amount"
    CONFIRMED = "refund_confirmed"
    PROCESSED = "refund_processed"
    REJECTED = "refund_rejected"
    CANCELLED = "refund_cancelled"
    SUBMITTED = "refund_submitted_for_review"


PENDING = {RefundStatus.AWAITING_CONFIRMATION, RefundStatus.AWAITING_AMOUNT}


@dataclass
class RefundState:
    status: RefundStatus = RefundStatus.IDLE
    amount: float | None = None  # validated amount awaiting confirmation
    recharge_id: str | None = None
    options: list[float] = field(default_factory=list)  # amounts offered while awaiting_amount
    suggested_amount: float | None = None  # single refundable recharge offered while awaiting_amount

    @property
    def pending(self) -> bool:
        return self.status in PENDING

    def finish(self, status: RefundStatus) -> None:
        self.status, self.options, self.suggested_amount = status, [], None


@dataclass
class AgentResult:
    text: str
    status: RefundStatus
    steps: list[str]
    needs_confirmation: bool = False
    handoff_to_rag: bool = False  # the customer left a pending amount question for a normal question
    amount: float | None = None


# ---------------------------------------------------------------------------
# Deterministic parsing of what the customer typed
# ---------------------------------------------------------------------------
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_CURRENCY_BEFORE = re.compile(r"(₹|\brs\.?|\binr)\s*$", re.I)
_CURRENCY_AFTER = re.compile(r"^\s*(rupees?|rs\b|inr\b|/-)", re.I)


@dataclass
class AmountMention:
    value: float
    currency: bool  # written with ₹ / Rs / INR / rupees


def _num(text: str) -> float:
    value = float(text.replace(",", ""))
    return int(value) if value.is_integer() else value


def parse_amounts(text: str) -> list[AmountMention]:
    found = []
    for m in _NUMBER.finditer(text):
        before, after = text[:m.start()], text[m.end():]
        currency = bool(_CURRENCY_BEFORE.search(before) or _CURRENCY_AFTER.search(after))
        if before and (before[-1].isalpha() or before[-1] == "_") and not currency:
            continue  # part of an ID such as rch_457
        found.append(AmountMention(_num(m.group().rstrip(",")), currency))
    return found


def pick_amount(mentions: list[AmountMention], recharge_amounts: set) -> float | None:
    """One amount from what the customer stated, or None if there is none or it's ambiguous."""
    values = list(dict.fromkeys(m.value for m in mentions))
    if len(values) == 1:
        return values[0]
    for narrowed in ([v for v in values if any(m.currency and m.value == v for m in mentions)],
                     [v for v in values if v in recharge_amounts]):
        if len(narrowed) == 1:
            return narrowed[0]
    return None


_YES = re.compile(r"(yes|y|yeah|yep)( please)?( proceed| go ahead| confirm| do it| refund it| confirm refund)?"
                  r"( please)?|proceed|confirm|confirm refund|go ahead|ok proceed|okay proceed")
_NO = re.compile(r"(no|n|nope|nah)( thanks| thank you)?( cancel| don't| do not| stop)?( it| the refund| refund)?"
                 r"|cancel( it| the refund| refund| this)?|don't proceed|do not proceed|stop|abort")


def parse_confirmation(text: str) -> str | None:
    """'yes', 'no', or None when the reply isn't an explicit answer (FR-36c, FR-36e)."""
    t = " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())
    if _YES.fullmatch(t):
        return "yes"
    if _NO.fullmatch(t):
        return "no"
    return None


def _fmt(amount: float) -> str:
    return f"{amount:g}"


def _amount_list(amounts: list[float]) -> str:
    shown = [inr(a) for a in amounts]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


# ---------------------------------------------------------------------------
class RefundAgent:
    def __init__(self, tools: RefundTools, get_llm=None):
        self.tools = tools
        self.get_llm = get_llm  # returns the shared chat model, or raises if not configured

    # --- New refund request ---------------------------------------------------
    def start(self, question: str, history: list[dict], state: RefundState, user_id: str) -> AgentResult:
        steps = ["[Intent] refund_request"]
        state.finish(RefundStatus.REQUESTED)
        llm_validations = self._run_llm(question, history, user_id, steps)

        account = self.tools.get_user_account(user_id)
        if not any(s.startswith("[Agent] get_user_account") for s in steps):
            steps.append("[Agent] get_user_account")
        if not account["found"]:
            return self._reject(state, steps, "I couldn't find your account.")

        recharge_amounts = {r["recharge_amount"] for r in account["recharges"]}
        mentions = parse_amounts(question)
        stated = {m.value for m in mentions}
        # Use the LLM's amount only if the customer actually wrote it (FR-44).
        amount = next((a for a in reversed(llm_validations) if a in stated), None)
        if amount is None:
            amount = pick_amount(mentions, recharge_amounts)
        if amount is None:
            return self._missing_amount(user_id, state, steps, ambiguous=bool(mentions))
        return self._validate_and_decide(amount, user_id, state, steps, llm_validations.get(amount))

    # --- Reply while a refund is pending --------------------------------------
    def resume(self, question: str, history: list[dict], state: RefundState, user_id: str,
               classify_intent) -> AgentResult:
        steps: list[str] = []
        answer = parse_confirmation(question)

        if state.status == RefundStatus.AWAITING_CONFIRMATION:
            if answer == "yes":
                return self._execute(user_id, state, steps)
            if answer == "no":
                steps.append("[User] cancelled")
                return self._cancel(state, steps)
            steps.append("[User] unclear reply; asking again")
            return AgentResult(
                f"I need a clear answer before I refund anything. Do you want to proceed with the refund of "
                f"**{inr(state.amount)}**? Reply **Yes, proceed** to confirm or **Cancel** to stop.",
                state.status, self._logged(steps), needs_confirmation=True, amount=state.amount)

        # awaiting_amount
        if answer == "no":
            steps.append("[User] cancelled")
            return self._cancel(state, steps)
        mentions = parse_amounts(question)
        amount = None
        if answer == "yes" and state.suggested_amount is not None and not mentions:
            amount = state.suggested_amount
        elif mentions:
            amounts = {r["recharge_amount"] for r in self.tools.get_user_account(user_id).get("recharges", [])}
            amount = pick_amount(mentions, amounts)
        if amount is not None:
            steps.append(f"[User] amount={_fmt(amount)}")
            return self._validate_and_decide(amount, user_id, state, steps)

        if not mentions and classify_intent(question) == REQUIREMENT_INQUIRY:
            steps.append("[Agent] customer asked something else; pending refund dropped")
            state.finish(RefundStatus.CANCELLED)
            return AgentResult("", state.status, self._logged(steps), handoff_to_rag=True)

        steps.append("[Agent] amount still unclear; asking again")
        options = state.options or ([state.suggested_amount] if state.suggested_amount else [])
        return AgentResult(f"Which amount would you like refunded: {_amount_list(options)}? "
                           f"You can also say **Cancel**.", state.status, self._logged(steps))

    # ------------------------------------------------------------------------
    def _run_llm(self, question: str, history: list[dict], user_id: str, steps: list[str]) -> dict:
        """Let the LLM call the read-only tools. Returns {amount: validate_refund result} for its calls."""
        validations: dict = {}
        if self.get_llm is None:
            return validations
        tools = {t.name: t for t in self.tools.llm_tools(user_id)}
        try:
            llm = self.get_llm().bind_tools(list(tools.values()))
        except Exception:
            steps.append("[Agent] LLM unavailable; using rule-based steps")
            return validations

        messages = [SystemMessage(AGENT_PROMPT)]
        for m in history[-config.HISTORY_TURNS * 2:]:  # FR-46, FR-50
            cls = HumanMessage if m["role"] == "user" else AIMessage
            messages.append(cls(m["content"][:400]))
        messages.append(HumanMessage(question))
        try:
            for _ in range(config.AGENT_MAX_STEPS):
                reply = llm.invoke(messages)
                messages.append(reply)
                if not reply.tool_calls:
                    break
                for call in reply.tool_calls:
                    t = tools.get(call["name"])
                    if t is None:  # e.g. an attempt to call process_refund
                        steps.append(f"[Agent] refused unknown tool {call['name']}")
                        result = {"error": f"Tool {call['name']} is not available."}
                    else:
                        result = t.invoke(call["args"])
                        if call["name"] == "validate_refund":
                            amount = result["requested_amount"]
                            validations[amount] = result
                            steps.append(f"[Agent] validate_refund(amount={_fmt(amount)})")
                        else:
                            steps.append(f"[Agent] {call['name']}")
                    messages.append(ToolMessage(json.dumps(result, ensure_ascii=False), tool_call_id=call["id"]))
        except Exception as exc:
            steps.append(f"[Agent] LLM error ({type(exc).__name__}); continuing with rule-based steps")
        return validations

    def _validate_and_decide(self, amount: float, user_id: str, state: RefundState, steps: list[str],
                             validation: dict | None = None) -> AgentResult:
        if validation is None:
            steps.append(f"[Agent] validate_refund(amount={_fmt(amount)})")
            validation = self.tools.validate_refund(user_id, amount)
        steps.append(f"[Validation] eligible={validation['eligible']}"
                     + ("" if validation["eligible"] else f" reason={validation['code']}"))
        if not validation["eligible"]:
            return self._reject(state, steps, validation["reason"], amount)

        state.finish(RefundStatus.VALIDATED)
        if amount <= config.REFUND_APPROVAL_LIMIT:  # FR-36
            state.status, state.amount, state.recharge_id = (RefundStatus.AWAITING_CONFIRMATION, amount,
                                                             validation["recharge_id"])
            steps.append("[Agent] awaiting_user_confirmation")
            return AgentResult(
                f"**Refund eligible: {inr(amount)}** (from your {inr(validation['recharge_amount'])} recharge).\n\n"
                f"Do you want to proceed with this refund? Reply **Yes, proceed** to confirm or **Cancel** to stop.",
                state.status, self._logged(steps), needs_confirmation=True, amount=amount)

        # Above the approval limit: hand to the Support Team; never process here (FR-37..FR-40a).
        steps.append(f"[Agent] above approval limit ({inr(amount)} > {inr(config.REFUND_APPROVAL_LIMIT)})")
        steps.append(f"[Agent] submit_to_support(amount={_fmt(amount)})")
        submission = self.tools.submit_to_support(user_id, amount)
        if not submission["submitted"]:
            return self._reject(state, steps, submission["reason"], amount)
        steps.append(f"[Support] submitted request={submission['request_id']}")
        state.finish(RefundStatus.SUBMITTED)
        return AgentResult(
            f"Your refund request of {inr(amount)} has been submitted to our Support Team "
            f"(reference **{submission['request_id']}**). They will review the request and process it or take "
            f"the necessary action.", state.status, self._logged(steps), amount=amount)

    def _execute(self, user_id: str, state: RefundState, steps: list[str]) -> AgentResult:
        steps.append("[User] confirmed")
        state.status = RefundStatus.CONFIRMED
        amount = state.amount
        steps.append(f"[Agent] process_refund(amount={_fmt(amount)})")
        result = self.tools.process_refund(user_id, amount)  # re-validates internally (GR-01, GR-08)
        if not result["success"]:
            steps.append("[Refund] failed")
            return self._reject(state, steps, result["reason"], amount)
        steps.append(f"[Refund] success refund_id={result['refund_id']}")
        state.finish(RefundStatus.PROCESSED)
        return AgentResult(
            f"Done. Your refund of **{inr(amount)}** has been processed. Refund reference: "
            f"**{result['refund_id']}**.\n\n_Demo: this is a simulated refund; no real payment was made._",
            state.status, self._logged(steps), amount=amount)

    def _missing_amount(self, user_id: str, state: RefundState, steps: list[str], ambiguous: bool) -> AgentResult:
        """FR-41..FR-44: never invent an amount."""
        steps.append("[Agent] amount " + ("ambiguous" if ambiguous else "not stated")
                     + "; checking refundable recharges")
        recharges = self.tools.get_user_account(user_id).get("recharges", [])
        refundable = sorted(self.tools.refundable_recharges(user_id), key=lambda r: -r["recharge_amount"])
        if not refundable:
            if not recharges:
                return self._reject(state, steps, "I couldn't find any recharges on your account.")
            reasons = " ".join(self.tools.validate_refund(user_id, r["recharge_amount"])["reason"] for r in recharges)
            return self._reject(state, steps, "None of your recent recharges can be refunded. " + reasons)

        if len(refundable) == 1 and not ambiguous:
            amount = refundable[0]["recharge_amount"]
            if amount <= config.REFUND_APPROVAL_LIMIT:
                steps.append(f"[Agent] one refundable recharge: {_fmt(amount)}")
                return self._validate_and_decide(amount, user_id, state, steps)
            state.finish(RefundStatus.AWAITING_AMOUNT)
            state.suggested_amount = amount
            steps.append("[Agent] awaiting_amount")
            return AgentResult(f"I can see one recharge that can be refunded: **{inr(amount)}**. Would you like "
                               f"to request a refund of {inr(amount)}? Reply **Yes** or **Cancel**.",
                               state.status, self._logged(steps))

        state.finish(RefundStatus.AWAITING_AMOUNT)
        state.options = [r["recharge_amount"] for r in refundable]
        steps.append("[Agent] awaiting_amount")
        lead = ("I can see more than one recent recharge" if len(refundable) > 1
                else "I can see one recharge that can be refunded")
        return AgentResult(f"{lead} - {_amount_list(state.options)}. Which amount would you like refunded?",
                           state.status, self._logged(steps))

    def _reject(self, state: RefundState, steps: list[str], reason: str, amount: float | None = None) -> AgentResult:
        steps.append("[Agent] refund_rejected")
        state.finish(RefundStatus.REJECTED)
        return AgentResult(f"I'm sorry, I can't process this refund. {reason} No refund has been made.",
                           state.status, self._logged(steps), amount=amount)

    def _cancel(self, state: RefundState, steps: list[str]) -> AgentResult:
        state.finish(RefundStatus.CANCELLED)
        return AgentResult("Okay, I've cancelled this request. No refund has been made.",
                           state.status, self._logged(steps))

    @staticmethod
    def _logged(steps: list[str]) -> list[str]:
        for s in steps:  # §12: workflow steps only, never model reasoning
            log.info(s)
        return steps
