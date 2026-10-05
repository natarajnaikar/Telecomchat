"""Simulated account data for the refund agent (agent_PRD.md §6.3).

There is no real CRM, billing or payment system. The signed-in user is config.DEMO_USER_ID,
treated as if it came from an authenticated session (FR-15..FR-17).
"""
import copy

USERS = {
    # The demonstration user (FR-18a): one recharge at the approval limit, one above it.
    "user_123": {
        "name": "Rahul",
        "plan": "Pro",
        "plan_amount": 999,
        "recharges": [
            {
                "recharge_id": "rch_456",
                "recharge_amount": 999,
                "recharge_status": "completed",
                "recharge_age_days": 2,
                "refund_status": "not_refunded",
            },
            {
                "recharge_id": "rch_457",
                "recharge_amount": 499,
                "recharge_status": "completed",
                "recharge_age_days": 1,
                "refund_status": "not_refunded",
            },
        ],
    },
    # Second demo account for Scenario 3: the only recharge is outside the 7-day window.
    "user_321": {
        "name": "Priya",
        "plan": "Basic",
        "plan_amount": 299,
        "recharges": [
            {
                "recharge_id": "rch_900",
                "recharge_amount": 299,
                "recharge_status": "completed",
                "recharge_age_days": 15,
                "refund_status": "not_refunded",
            },
        ],
    },
}


class AccountStore:
    """A private copy of USERS that simulated refunds update (FR-33).

    Each chat session gets its own store, so one demo never changes another.
    """

    def __init__(self, users: dict | None = None):
        self.users = copy.deepcopy(USERS if users is None else users)
        self.refunds: list[dict] = []  # simulated refund ledger
        self.support_requests: list[dict] = []  # requests handed to the Support Team
