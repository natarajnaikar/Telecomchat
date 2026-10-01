"""Ingest data/faq.csv into the `faq` collection (FR-14: 1 row = 1 document)."""
import csv

from langchain_core.documents import Document

import config
from knowledge_store import rebuild_collection

COLLECTION = "faq"


def load_faq_docs(path=config.FAQ_CSV) -> tuple[list[Document], list[str]]:
    docs, ids = [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            question, answer = row["question"].strip(), row["answer"].strip()
            if not question or not answer:
                continue
            docs.append(Document(
                page_content=f"Q: {question}\nA: {answer}",
                metadata={
                    "source": "FAQ",
                    "doc_id": f"FAQ #{row['id']}",
                    "category": row.get("category", ""),
                    "title": question,
                },
            ))
            ids.append(f"faq-{row['id']}")
    return docs, ids


def ingest() -> int:
    return rebuild_collection(COLLECTION, *load_faq_docs())


if __name__ == "__main__":
    print(f"Ingested {ingest()} FAQ entries into '{COLLECTION}'.")
