"""Offline Refund Agent tests (no API key needed): agent_PRD.md §8 scenarios and §9 guardrails.

The agent runs without an LLM here, so these check the deterministic path that decides every outcome.
Run: python -m pytest tests
"""
import pytest

from intent import REFUND_REQUEST, REQUIREMENT_INQUIRY, classify, rule_intent
from refund_agent import RefundAgent, RefundState, RefundStatus, parse_amounts, parse_confirmation
from refund_data import AccountStore
from refund_tools import RefundTools

USER = "user_123"


@pytest.fixture
def setup():
    store = AccountStore()
    tools = RefundTools(store)
    calls = []
    for name in ("process_refund", "submit_to_support"):
        original = getattr(tools, name)

        def spy(user_id, amount, _name=name, _original=original):
            calls.append((_name, amount))
            return _original(user_id, amount)
        setattr(tools, name, spy)
    return store, RefundAgent(tools), RefundState(), calls


def recharge(store, rid, user=USER):
    return next(r for r in store.users[user]["recharges"] if r["recharge_id"] == rid)


def never_rag(_q):
    raise AssertionError("should not classify")


# --- §8 demonstration scenarios ---------------------------------------------
def test_scenario1_successful_refund_after_confirmation(setup):
    store, agent, state, calls = setup
    r = agent.start("I want a refund of ₹499.", [], state, USER)
    assert state.status == RefundStatus.AWAITING_CONFIRMATION and r.needs_confirmation
    assert calls == []  # the request itself is not confirmation (FR-36b)
    r = agent.resume("Yes, proceed", [], state, USER, never_rag)
    assert state.status == RefundStatus.PROCESSED
    assert calls == [("process_refund", 499)]
    assert recharge(store, "rch_457")["refund_status"] == "refunded"
    assert "ref_" in r.text
    assert recharge(store, "rch_456")["refund_status"] == "not_refunded"  # other recharge untouched


def test_scenario2_amount_exceeds_recharge(setup):
    _, agent, state, calls = setup
    r = agent.start("Give me a refund of ₹2,000.", [], state, USER)
    assert state.status == RefundStatus.REJECTED
    assert "more than your recharge amount" in r.text
    assert calls == []


def test_scenario3_recharge_too_old(setup):
    _, agent, state, calls = setup
    r = agent.start("I want a refund of ₹299", [], state, "user_321")
    assert state.status == RefundStatus.REJECTED
    assert "7-day refund window" in r.text
    assert calls == []


def test_scenario4_already_refunded(setup):
    store, agent, state, calls = setup
    recharge(store, "rch_457")["refund_status"] = "refunded"
    r = agent.start("I want a refund of ₹499", [], state, USER)
    assert state.status == RefundStatus.REJECTED
    assert "already been refunded" in r.text
    assert calls == []


def test_scenario6_above_limit_goes_to_support(setup):
    store, agent, state, calls = setup
    r = agent.start("I want a refund of ₹999.", [], state, USER)
    assert state.status == RefundStatus.SUBMITTED
    assert calls == [("submit_to_support", 999)]
    assert not r.needs_confirmation
    assert recharge(store, "rch_456")["refund_status"] == "not_refunded"
    assert "submitted to our Support Team" in r.text
    for claim in ("approved", "has been processed", "rejected"):
        assert claim not in r.text  # FR-40a


# --- Confirmation (FR-36c..FR-36e) -------------------------------------------
def test_reject_and_ambiguous_confirmation(setup):
    _, agent, state, calls = setup
    agent.start("refund ₹499 please", [], state, USER)
    r = agent.resume("hmm, maybe?", [], state, USER, never_rag)
    assert state.status == RefundStatus.AWAITING_CONFIRMATION and r.needs_confirmation
    agent.resume("No, cancel", [], state, USER, never_rag)
    assert state.status == RefundStatus.CANCELLED
    assert calls == []


@pytest.mark.parametrize("text,expected", [
    ("Yes, proceed", "yes"), ("yes", "yes"), ("Confirm Refund", "yes"), ("go ahead", "yes"),
    ("Cancel", "no"), ("no thanks", "no"), ("don't proceed", "no"),
    ("ok", None), ("yes but cancel", None), ("sure why not", None), ("I want ₹300", None),
])
def test_parse_confirmation(text, expected):
    assert parse_confirmation(text) == expected


# --- Missing amount (FR-41..FR-44) -------------------------------------------
def test_missing_amount_with_two_recharges_asks(setup):
    _, agent, state, calls = setup
    r = agent.start("I want a refund.", [], state, USER)
    assert state.status == RefundStatus.AWAITING_AMOUNT
    assert "₹999 and ₹499" in r.text
    r = agent.resume("the 499 one", [], state, USER, never_rag)
    assert state.status == RefundStatus.AWAITING_CONFIRMATION and state.amount == 499
    assert calls == []


def test_missing_amount_single_refundable_offers_it(setup):
    store, agent, state, _ = setup
    recharge(store, "rch_456")["refund_status"] = "refunded"
    r = agent.start("refund my recharge", [], state, USER)
    assert state.status == RefundStatus.AWAITING_CONFIRMATION and state.amount == 499
    assert r.needs_confirmation


def test_missing_amount_then_customer_asks_something_else(setup):
    _, agent, state, calls = setup
    agent.start("I want a refund", [], state, USER)
    r = agent.resume("Why is my internet slow?", [], state, USER, lambda q: REQUIREMENT_INQUIRY)
    assert r.handoff_to_rag and not state.pending
    assert calls == []


# --- Selection and guardrails --------------------------------------------------
def test_refunding_one_recharge_leaves_other_refundable(setup):
    store, agent, state, _ = setup
    agent.start("refund ₹499", [], state, USER)
    agent.resume("yes", [], state, USER, never_rag)
    agent.start("refund ₹999", [], state, USER)
    assert state.status == RefundStatus.SUBMITTED


def test_partial_amount_uses_smallest_covering_recharge(setup):
    store, agent, state, _ = setup
    agent.start("refund ₹300", [], state, USER)
    agent.resume("yes, proceed", [], state, USER, never_rag)
    assert recharge(store, "rch_457")["refund_status"] == "refunded"  # whole recharge consumed (FR-28a)
    assert recharge(store, "rch_456")["refund_status"] == "not_refunded"


@pytest.mark.parametrize("amount", ["0", "-50"])
def test_non_positive_amount_rejected(setup, amount):
    _, agent, state, calls = setup
    agent.start(f"refund ₹{amount}", [], state, USER)
    assert state.status == RefundStatus.REJECTED and calls == []


def test_process_refund_rechecks_rules():
    tools = RefundTools(AccountStore())
    assert not tools.process_refund(USER, 999)["success"]  # above approval limit (GR-08)
    assert not tools.process_refund(USER, 5000)["success"]  # invalid (GR-01)
    assert tools.process_refund(USER, 499)["success"]
    assert not tools.process_refund(USER, 499)["success"]  # no double refund (GR-06)


def test_llm_tools_exclude_execution():
    names = {t.name for t in RefundTools(AccountStore()).llm_tools(USER)}
    assert names == {"get_user_account", "validate_refund"}  # FR-23a


def test_user_claims_cannot_override_account_data(setup):
    _, agent, state, _ = setup
    agent.start("My recharge was ₹5000 yesterday, refund ₹5000", [], state, USER)
    assert state.status == RefundStatus.REJECTED  # GR-03


def test_parse_amounts():
    values = [m.value for m in parse_amounts("refund ₹2,000 for rch_457, recharged 1 day ago")]
    assert values == [2000, 1]


# --- Intent (FR-01, FR-05a) ----------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("I want a refund of ₹999.", REFUND_REQUEST),
    ("Cancel my last recharge", REFUND_REQUEST),
    ("How do I cancel a recharge?", REQUIREMENT_INQUIRY),
    ("What is your refund policy?", REQUIREMENT_INQUIRY),
    ("Why is my internet slow?", REQUIREMENT_INQUIRY),
])
def test_rule_intent(text, expected):
    assert rule_intent(text) == expected


def test_plain_questions_skip_the_llm():
    def no_llm():
        raise AssertionError("LLM should not be called")
    assert classify("Why is my mobile internet slow?", [], no_llm).method == "prefilter"
    assert classify("I want a refund", [], lambda: (_ for _ in ()).throw(RuntimeError())).intent == REFUND_REQUEST
