"""Ingest PDF guides from data/*.pdf into the `guides` collection.

FR-16 (PRD.md): 600-char chunks with 100-char overlap.
FR-13 (NovaCell PRD): chunks keep their section heading and page number for citation.
"""
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

import config
from knowledge_store import rebuild_collection

COLLECTION = "guides"
SECTION_RE = re.compile(r"^(\d{1,2})\.\s+([A-Z].{3,80})$")
NOISE_RE = re.compile(r"^(Page \d+|.*- Internal Use Only)$")


def _page_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not NOISE_RE.match(ln.strip())]


def load_guide_docs(guides_dir=config.GUIDES_DIR) -> tuple[list[Document], list[str]]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.GUIDE_CHUNK_SIZE, chunk_overlap=config.GUIDE_CHUNK_OVERLAP
    )
    docs, ids = [], []
    for pdf in sorted(guides_dir.glob("*.pdf")):
        section = "Introduction"
        for page_no, page in enumerate(PdfReader(pdf).pages, start=1):
            # Group each page's text by section heading so chunks never straddle sections.
            blocks: list[tuple[str, list[str]]] = [(section, [])]
            for line in _page_lines(page.extract_text() or ""):
                m = SECTION_RE.match(line)
                if m:
                    section = f"§{m.group(1)} {m.group(2).strip()}"
                    blocks.append((section, []))
                else:
                    blocks[-1][1].append(line)

            for sec, lines in blocks:
                body = " ".join(lines).strip()
                if len(body) < 40:
                    continue
                for i, chunk in enumerate(splitter.split_text(body)):
                    docs.append(Document(
                        page_content=f"[{sec}] {chunk}",
                        metadata={
                            "source": "GUIDE",
                            "doc_id": f"{pdf.stem} {sec}, p.{page_no}",
                            "category": "guide",
                            "title": sec,
                            "page": page_no,
                        },
                    ))
                    ids.append(f"{pdf.stem}-p{page_no}-{len(ids)}")
    return docs, ids


def ingest() -> int:
    return rebuild_collection(COLLECTION, *load_guide_docs())


if __name__ == "__main__":
    print(f"Ingested {ingest()} guide chunks into '{COLLECTION}'.")
