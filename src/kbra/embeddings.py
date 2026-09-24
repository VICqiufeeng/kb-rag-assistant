"""向量化：bge-small-zh-v1.5 走本地路径加载，查询侧加 bge 中文指令前缀。"""
from __future__ import annotations

import numpy as np

from .config import EMBED_MODEL

# bge 中文系列推荐：检索用 query 加指令前缀，passage 不加
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_model = None


def get_model(device: str = "cuda"):
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(str(EMBED_MODEL), device=device)
    return _model


def encode_docs(texts: list[str], batch_size: int = 64) -> np.ndarray:
    m = get_model()
    vecs = m.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                    show_progress_bar=len(texts) > 256)
    return np.asarray(vecs, dtype=np.float32)


def encode_query(text: str) -> np.ndarray:
    m = get_model()
    return np.asarray(m.encode([QUERY_INSTRUCTION + text], normalize_embeddings=True),
                      dtype=np.float32)[0]
