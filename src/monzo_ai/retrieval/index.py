"""Phase 6: build/load the vector (FAISS) + keyword (BM25) retrieval index.

Embeddings are computed once with a local sentence-transformers model (no
API key, no per-query cost, fully reproducible offline) and stored in a
FAISS flat index. BM25 isn't persisted directly — it's cheap enough to
rebuild in-memory from the saved chunk text on load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import faiss
import numpy as np
import pandas as pd
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from monzo_ai.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_model_cache: dict[str, SentenceTransformer] = {}


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class RetrievalIndex:
    chunks: pd.DataFrame  # CHUNK_COLUMNS; row position matches the FAISS/BM25 index
    faiss_index: faiss.Index
    bm25: BM25Okapi
    model_name: str


def load_embedding_model(model_name: str = DEFAULT_MODEL_NAME) -> SentenceTransformer:
    if model_name not in _model_cache:
        logger.info("Loading embedding model %s", model_name)
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


def embed_texts(texts: list[str], model_name: str = DEFAULT_MODEL_NAME) -> np.ndarray:
    model = load_embedding_model(model_name)
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=len(texts) > 50)
    return np.asarray(embeddings, dtype="float32")


def build_index(chunks_df: pd.DataFrame, model_name: str = DEFAULT_MODEL_NAME) -> RetrievalIndex:
    if chunks_df.empty:
        raise ValueError("chunks_df is empty; nothing to index.")

    chunks_df = chunks_df.reset_index(drop=True)
    embeddings = embed_texts(chunks_df["text"].tolist(), model_name=model_name)

    faiss_index = faiss.IndexFlatIP(embeddings.shape[1])
    faiss_index.add(embeddings)

    tokenized = [_tokenize(t) for t in chunks_df["text"].tolist()]
    bm25 = BM25Okapi(tokenized)

    logger.info("Built retrieval index: %d chunks, %d-dim embeddings", len(chunks_df), embeddings.shape[1])
    return RetrievalIndex(chunks=chunks_df, faiss_index=faiss_index, bm25=bm25, model_name=model_name)


def save_index(index: RetrievalIndex, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index.faiss_index, str(output_dir / "vector_index.faiss"))
    index.chunks.to_parquet(output_dir / "chunks.parquet", index=False)
    (output_dir / "model_name.txt").write_text(index.model_name, encoding="utf-8")
    logger.info("Saved retrieval index (%d chunks) to %s", len(index.chunks), output_dir)


def load_index(input_dir: Path) -> RetrievalIndex:
    chunks = pd.read_parquet(input_dir / "chunks.parquet")
    faiss_index = faiss.read_index(str(input_dir / "vector_index.faiss"))
    model_name = (input_dir / "model_name.txt").read_text(encoding="utf-8").strip()
    tokenized = [_tokenize(t) for t in chunks["text"].tolist()]
    bm25 = BM25Okapi(tokenized)
    return RetrievalIndex(chunks=chunks, faiss_index=faiss_index, bm25=bm25, model_name=model_name)
