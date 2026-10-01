"""Merged retriever over the three knowledge collections (FR-06..FR-08, FR-10, FR-15, FR-16).

To add a knowledge source (NFR-06): write an ingest_<name>.py that fills a collection,
then register it in COLLECTIONS below.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import config
from knowledge_store import get_collection

# Order = source precedence when sources conflict: official content before historical tickets.
COLLECTIONS = {
    "FAQ": "faq",
    "GUIDES": "guides",
    "TICKETS": "tickets",
}


@dataclass
class RetrievedDoc:
    label: str  # FAQ / GUIDES / TICKETS
    doc_id: str
    title: str
    content: str
    score: float

    def as_dict(self) -> dict:
        return {"label": self.label, "doc_id": self.doc_id, "title": self.title,
                "score": round(self.score, 3), "content": self.content}


class MergedRetriever:
    def __init__(self, k: int = config.TOP_K_PER_COLLECTION, min_relevance: float = config.MIN_RELEVANCE):
        self.k = k
        self.min_relevance = min_relevance
        self.stores = {label: get_collection(name) for label, name in COLLECTIONS.items()}

    def _search(self, label: str, query: str) -> list[RetrievedDoc]:
        hits = self.stores[label].similarity_search_with_relevance_scores(query, k=self.k)
        return [
            RetrievedDoc(label, d.metadata.get("doc_id", "?"), d.metadata.get("title", ""),
                         d.page_content, float(score))
            for d, score in hits
        ]

    def retrieve(self, query: str) -> tuple[list[RetrievedDoc], float]:
        """Return (relevant docs grouped in precedence order, best score across all sources)."""
        with ThreadPoolExecutor(max_workers=len(self.stores)) as pool:
            results = dict(zip(self.stores, pool.map(lambda lbl: self._search(lbl, query), self.stores)))
        all_docs = [d for label in COLLECTIONS for d in results[label]]
        best = max((d.score for d in all_docs), default=0.0)
        kept = [d for d in all_docs if d.score >= self.min_relevance]
        return kept, best


def format_context(docs: list[RetrievedDoc]) -> str:
    if not docs:
        return "(no relevant documents found)"
    sections = []
    for label in COLLECTIONS:
        group = [d for d in docs if d.label == label]
        if group:
            body = "\n\n".join(f"[{d.doc_id}]\n{d.content}" for d in group)
            sections.append(f"=== {label} ===\n{body}")
    return "\n\n".join(sections)
