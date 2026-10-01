"""Re-index all knowledge sources. Safe to re-run (FR-17)."""
import ingest_faq
import ingest_guides
import ingest_tickets


def ingest_all() -> dict[str, int]:
    return {
        "faq": ingest_faq.ingest(),
        "tickets": ingest_tickets.ingest(),
        "guides": ingest_guides.ingest(),
    }


if __name__ == "__main__":
    for name, count in ingest_all().items():
        print(f"{name:8s} {count} documents")
