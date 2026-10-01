"""Shared helpers for the Chroma vector store (used by ingest scripts and the retriever)."""
from functools import lru_cache

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

import config


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=config.EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True},
    )


def get_collection(name: str) -> Chroma:
    return Chroma(
        collection_name=name,
        embedding_function=get_embeddings(),
        persist_directory=str(config.CHROMA_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )


def rebuild_collection(name: str, docs: list[Document], ids: list[str]) -> int:
    """Drop and recreate a collection so ingest scripts are idempotent (FR-17)."""
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    if name in [c.name for c in client.list_collections()]:
        client.delete_collection(name)
    store = get_collection(name)
    if docs:
        store.add_documents(docs, ids=ids)
    return len(docs)
