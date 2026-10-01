"""CLI REPL for the NovaCell Support Assistant (FR-18, FR-19).

Run:  python main.py     (type 'quit' to exit, 'clear' to reset)
"""
import sys

import escalation
from assistant import ConversationState, SupportAssistant


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("Loading knowledge base…")
    bot = SupportAssistant()
    state, history = ConversationState(), []
    print("NovaCell Support Assistant. I can't see your account. Type 'quit' to exit, 'clear' to reset.\n")

    while True:
        try:
            question = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() == "quit":
            break
        if question.lower() == "clear":
            state, history = ConversationState(), []
            print("(conversation cleared)\n")
            continue

        try:
            turn = bot.respond(question, history, state)
        except RuntimeError as exc:
            print(f"Error: {exc}")
            break
        if turn.notice:
            print(f"! {turn.notice}")
        print("Bot: ", end="", flush=True)
        printed = ""
        for chunk in turn.stream():
            if not turn.replaced:
                printed += chunk
                print(chunk, end="", flush=True)
        if turn.replaced:
            # A guardrail withdrew the streamed answer; make the replacement explicit.
            print(("\n[Answer withdrawn] " if printed.strip() else "") + turn.text, end="")
        print()

        if turn.kind in {"answer", "boundary"} and turn.cited_sources:
            print("Sources: " + ", ".join(dict.fromkeys(s.doc_id for s in turn.cited_sources)))
        if turn.offer_human:
            print("→ " + escalation.channel_message().replace("**", ""))
        print()
        history += [{"role": "user", "content": question}, {"role": "assistant", "content": turn.text}]

    print("Goodbye!")


if __name__ == "__main__":
    main()
