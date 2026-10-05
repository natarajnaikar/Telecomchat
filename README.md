# NovaCell Support Assistant (RAG chatbot)

A grounded customer-care chatbot for Tier-1 telecom questions, built from `PRD.md` (technical spec) and
`PRD_NovaCell_Support_Assistant.md` (product requirements). It answers only from three knowledge sources: the FAQ,
resolved support tickets and the PDF guide. It cites its sources, declines when it isn't sure, and hands off to a
person when needed.

## Setup

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
copy .env.example .env        # then add your GROQ_API_KEY
.venv\Scripts\python ingest_all.py
```

## Run

```bash
.venv\Scripts\streamlit run app.py     # web chat + Knowledge Admin + Analytics pages
.venv\Scripts\python main.py           # CLI (type quit to exit)
.venv\Scripts\python eval.py           # behaviour eval against the PRD examples (needs API key)
.venv\Scripts\python -m pytest tests   # offline guardrail tests
```

## How a question is handled

1. **Guardrails** (`guardrails.py`): detect emergencies, prompt injection, high-risk topics (fraud, refunds,
   unauthorised changes), requests for a person, personal-account questions, and shared PINs/card numbers.
   PII is redacted before anything is logged.
2. **Follow-up rewrite**: with chat history, the question is rewritten into a standalone query (last 6 turns).
3. **Retrieval** (`retriever.py`): top 3 from each of `faq`, `guides`, `tickets` in Chroma, in parallel, using
   local `all-MiniLM-L6-v2` embeddings. Chunks below `MIN_RELEVANCE` are dropped. If the best score is below
   `CONFIDENCE_THRESHOLD`, the bot says it isn't sure (or that the question is out of scope) without calling the LLM.
4. **Generation** (`assistant.py`): `qwen/qwen3.6-27b` on Groq, temperature 0, `reasoning_effort="none"`, streamed.
   The prompt allows only context facts, requires inline citations, and has the model reply `NO_ANSWER` if the
   context doesn't cover the question.
5. **Checks after generation**: every number in the answer must appear in the retrieved text, and every price must
   match a price in it. Otherwise the streamed answer is withdrawn and replaced with a safe fallback.
6. **Escalation** (`escalation.py`): offered on a request for a person, high-risk topics, two unanswered turns in a
   row, repeated frustration, a 👎 marked "didn't solve my problem", or account-specific questions. Live chat is
   offered in staffed hours (8am–10pm), otherwise a callback form (consent required). The handoff package
   (redacted transcript, triggers, topic, sources tried, feedback) goes to `logs/handoffs.jsonl`.

## Refund Agent (`agent_PRD.md`)

Every message is first classified as `requirement_inquiry` (the RAG pipeline above, unchanged) or
`refund_request` (the Refund Agent). Emergency and prompt-injection checks still run first.

- **Intent** (`intent.py`): messages with no refund-like words go straight to RAG with no extra LLM call.
  Otherwise the LLM classifies them; "cancel my recharge" is a refund request, "how do I cancel a recharge?"
  is an inquiry. Keyword rules are the fallback if the LLM is unavailable.
- **Data** (`refund_data.py`): hard-coded accounts. The signed-in user is `DEMO_USER_ID` (`user_123`, Rahul:
  ₹999 and ₹499 recharges). `user_321` has a 15-day-old recharge for the "too old" scenario. Each chat session
  works on its own copy; switch user or reset it under **Demo account** in the sidebar.
- **Tools** (`refund_tools.py`): `get_user_account`, `validate_refund`, `process_refund` (simulated) and
  `submit_to_support`. The LLM can call only the first two, with the user ID bound from the session.
- **Agent** (`refund_agent.py`): the LLM calls the read-only tools; application code then decides:
  - the amount must be one the customer typed (never invented); with no amount, one refundable recharge is
    offered, several are listed for the customer to pick;
  - eligibility comes only from `validate_refund` (completed, ≤ 7 days, 0 < amount ≤ recharge, not already
    refunded);
  - **≤ ₹499**: ask for explicit confirmation (buttons or "Yes, proceed"); unclear replies are asked again;
    only then `process_refund`, which re-checks every rule itself;
  - **> ₹499**: `submit_to_support`; the reply says the Support Team will review it, nothing more.
  Replies are fixed templates filled from tool results. Each turn's steps (`[Intent]`, `[Agent]`,
  `[Validation]`, `[Refund]`) show under **Agent steps** and go to `logs/interactions.jsonl`.
- **Clear conversation** drops the history and any pending refund; simulated account data is kept.

Try: "I want a refund of ₹499" → Confirm; "I want a refund of ₹999"; "Give me a refund of ₹2,000"; "I want a
refund"; then ₹499 again (already refunded); switch to Priya and ask for ₹299 (outside the window).

## Keeping knowledge current (no code release)

- **Knowledge Admin page**: edit/add/retire FAQ rows, import/export the FAQ CSV, upload/retire PDF guides,
  include or exclude tickets. Every save re-indexes immediately.
- **Or from scripts**: edit `data/faq.csv`, `data/tickets.db` or add a PDF to `data/`, then run
  `ingest_faq.py`, `ingest_tickets.py`, `ingest_guides.py` or `ingest_all.py`. Re-runs are idempotent.
- Only tickets with `status = 'resolved'` are indexed.
- New source: write `ingest_<name>.py` and register the collection in `COLLECTIONS` in `retriever.py`.

## Logs

- `logs/interactions.jsonl`: per-turn trace (redacted question, rewritten query, retrieved IDs and scores,
  outcome, triggers, model, prompt version, latency), feedback and escalations. Feeds the Analytics page.
- `logs/handoffs.jsonl`: handoff packages, plus callback contact details given with consent.

## v1 limits

- The live-agent platform is not integrated (PRD open question Q2), so handoffs are recorded to a file.
- Guardrail flags are keyword/regex based, which is easy to audit but misses paraphrases.
- Refund amounts must be typed as digits ("₹499", "499 rupees"); "four ninety-nine" makes the agent ask.
- The seed content uses "MyTelecom app" and "611" (PRD open question Q3). The bot repeats what the sources say.
- The Knowledge Admin page has no roles. Set `ADMIN_PASSCODE` in `.env` to put a passcode on it.
