"""Refund tools (agent_PRD.md §6.4-§6.7).

Every eligibility rule and the approval limit are plain code here, never the LLM (FR-29, FR-30, FR-36a).
Only get_user_account and validate_refund are offered to the LLM; process_refund and
submit_to_support are called by the agent's own code after its checks (FR-23a).
"""
import copy
import uuid

from langchain_core.tools import tool

import config
from refund_data import AccountStore


def inr(amount: float) -> str:
    return f"₹{amount:,.0f}" if float(amount).is_integer() else f"₹{amount:,.2f}"


def _is_open(r: dict) -> bool:
    """A recharge that could still be refunded."""
    return (r["recharge_status"] == "completed" and r["recharge_age_days"] <= config.REFUND_WINDOW_DAYS
            and r["refund_status"] != "refunded")


def select_recharge(recharges: list[dict], amount: float) -> dict | None:
    """Pick the recharge a refund of `amount` applies to (FR-27a), deterministically.

    1. A recharge whose amount matches exactly, even if already refunded.
    2. Otherwise the smallest recharge that covers the amount, preferring refundable ones.
    3. Otherwise the largest recharge, so validation can report that the amount exceeds it.
    Ties go to the most recent recharge.
    """
    if not recharges:
        return None
    exact = [r for r in recharges if r["recharge_amount"] == amount]
    if exact:
        return min(exact, key=lambda r: (not _is_open(r), r["recharge_age_days"]))
    covering = [r for r in recharges if r["recharge_amount"] >= amount]
    if covering:
        return min(covering, key=lambda r: (not _is_open(r), r["recharge_amount"], r["recharge_age_days"]))
    return max(recharges, key=lambda r: (r["recharge_amount"], -r["recharge_age_days"]))


class RefundTools:
    def __init__(self, store: AccountStore):
        self.store = store

    def _recharges(self, user_id: str) -> list[dict]:
        return self.store.users.get(user_id, {}).get("recharges", [])

    # --- FR-19 ---------------------------------------------------------------
    def get_user_account(self, user_id: str) -> dict:
        user = self.store.users.get(user_id)
        if user is None:
            return {"found": False, "user_id": user_id}
        return {"found": True, "user_id": user_id, **copy.deepcopy(user)}

    def refundable_recharges(self, user_id: str) -> list[dict]:
        return [r for r in self._recharges(user_id) if _is_open(r)]

    # --- FR-20, FR-24..FR-30 -------------------------------------------------
    def validate_refund(self, user_id: str, amount: float) -> dict:
        result = {"eligible": False, "requested_amount": amount, "recharge_amount": None, "recharge_id": None}

        def reject(code: str, reason: str) -> dict:
            return {**result, "code": code, "reason": reason}

        if amount is None or amount <= 0:  # FR-26
            return reject("invalid_amount", "The refund amount must be greater than zero.")
        if user_id not in self.store.users:
            return reject("no_account", "I couldn't find your account.")
        recharge = select_recharge(self._recharges(user_id), amount)
        if recharge is None:
            return reject("no_recharge", "I couldn't find any recharges on your account.")
        result.update(recharge_amount=recharge["recharge_amount"], recharge_id=recharge["recharge_id"])

        if recharge["refund_status"] == "refunded":  # FR-28
            return reject("already_refunded",
                          f"Your {inr(recharge['recharge_amount'])} recharge has already been refunded.")
        if recharge["recharge_status"] != "completed":  # FR-24
            return reject("not_completed",
                          f"Your {inr(recharge['recharge_amount'])} recharge hasn't completed "
                          f"(status: {recharge['recharge_status']}), so it can't be refunded.")
        if recharge["recharge_age_days"] > config.REFUND_WINDOW_DAYS:  # FR-25
            return reject("outside_window",
                          f"Your {inr(recharge['recharge_amount'])} recharge was "
                          f"{recharge['recharge_age_days']} days ago, outside the "
                          f"{config.REFUND_WINDOW_DAYS}-day refund window.")
        if amount > recharge["recharge_amount"]:  # FR-27
            return reject("exceeds_recharge",
                          f"The requested amount of {inr(amount)} is more than your recharge amount of "
                          f"{inr(recharge['recharge_amount'])}.")
        return {**result, "eligible": True, "code": "eligible", "reason": "Refund is eligible."}

    # --- FR-21, FR-31..FR-35 -------------------------------------------------
    def process_refund(self, user_id: str, amount: float) -> dict:
        """Simulated refund. Re-checks validation and the approval limit itself (GR-01, GR-08)."""
        check = self.validate_refund(user_id, amount)
        if not check["eligible"]:
            return {"success": False, "amount": amount, "status": "not_refunded", "reason": check["reason"]}
        if amount > config.REFUND_APPROVAL_LIMIT:
            return {"success": False, "amount": amount, "status": "not_refunded",
                    "reason": "Above the approval limit; this refund must go to the Support Team."}
        recharge = next(r for r in self._recharges(user_id) if r["recharge_id"] == check["recharge_id"])
        refund_id = "ref_" + uuid.uuid4().hex[:6]
        recharge["refund_status"] = "refunded"  # a refund consumes the whole recharge (FR-28a)
        recharge["refund_id"] = refund_id
        record = {"success": True, "refund_id": refund_id, "amount": amount, "status": "refunded",
                  "recharge_id": recharge["recharge_id"]}
        self.store.refunds.append({"user_id": user_id, **record})
        return record

    # --- FR-21a, FR-38 -------------------------------------------------------
    def submit_to_support(self, user_id: str, amount: float) -> dict:
        """Hand an eligible refund above the limit to the Support Team. Changes no refund status."""
        check = self.validate_refund(user_id, amount)
        if not check["eligible"]:
            return {"submitted": False, "amount": amount, "reason": check["reason"]}
        request_id = "sup_" + uuid.uuid4().hex[:6]
        record = {"submitted": True, "request_id": request_id, "amount": amount,
                  "recharge_id": check["recharge_id"], "status": "submitted_for_review"}
        self.store.support_requests.append({"user_id": user_id, **record})
        return record

    # --- What the LLM may call (FR-23a) --------------------------------------
    def llm_tools(self, user_id: str) -> list:
        """The user ID is bound from the session, so the LLM and the customer can't change it (GR-03)."""

        @tool
        def get_user_account() -> dict:
            """Return the signed-in customer's plan and recent recharges."""
            return self.get_user_account(user_id)

        @tool
        def validate_refund(amount: float) -> dict:
            """Check a refund of `amount` rupees against the refund rules. Returns eligibility and the reason."""
            return self.validate_refund(user_id, amount)

        return [get_user_account, validate_refund]
