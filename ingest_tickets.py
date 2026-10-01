"""Ingest resolved tickets from data/tickets.db into the `tickets` collection.

FR-15: 1 ticket = 1 document. FR-11: only `resolved` tickets with a resolution.
FR-12: text is passed through PII redaction so no customer identifiers reach the index.
"""
import sqlite3

from langchain_core.documents import Document

import config
from guardrails import redact_pii
from knowledge_store import rebuild_collection

COLLECTION = "tickets"


def load_ticket_docs(path=config.TICKETS_DB) -> tuple[list[Document], list[str]]:
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT ticket_id, category, issue_type, description, resolution FROM tickets "
        "WHERE status = 'resolved' AND TRIM(COALESCE(resolution, '')) <> ''"
    ).fetchall()
    conn.close()

    docs, ids = [], []
    for ticket_id, category, issue_type, description, resolution in rows:
        docs.append(Document(
            page_content=(
                f"Issue: {issue_type}\n"
                f"Description: {redact_pii(description)}\n"
                f"Resolution: {redact_pii(resolution)}"
            ),
            metadata={
                "source": "TICKET",
                "doc_id": ticket_id,
                "category": category,
                "title": issue_type,
            },
        ))
        ids.append(ticket_id)
    return docs, ids


def ingest() -> int:
    return rebuild_collection(COLLECTION, *load_ticket_docs())


if __name__ == "__main__":
    print(f"Ingested {ingest()} resolved tickets into '{COLLECTION}'.")
