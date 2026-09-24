"""索引构建与检索：稠密向量（bge）+ 词法（jieba + BM25），RRF 融合。

1.6 万块量级语料直接用 numpy 全量算分，不引入向量数据库；
语料规模到 10 万块量级再换 faiss 才有意义。
"""
from __future__ import annotations

import json
import pickle
import re
from datetime import datetime

import numpy as np

from .config import CHUNKS, EMBED_MODEL, INDEX_DIR, META, VECTORS
from .embeddings import encode_docs, encode_query

PUNCT_RE = re.compile(r"^[\s\W]+$")


def tokenize(text: str) -> list[str]:
    import jieba

    return [t.lower() for t in jieba.lcut(text) if not PUNCT_RE.match(t)]


def build(chunks: list[dict]) -> dict:
    """切块结果落盘 + 建稠密向量与 BM25 分词索引。"""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    with CHUNKS.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    vecs = encode_docs([c["embed_text"] for c in chunks])
    np.save(VECTORS, vecs.astype(np.float32))
    with (INDEX_DIR / "bm25.pkl").open("wb") as f:
        pickle.dump([tokenize(c["embed_text"]) for c in chunks], f)

    meta = {
        "chunks": len(chunks),
        "docs": len({c["doc_id"] for c in chunks}),
        "dim": int(vecs.shape[1]),
        "embed_model": EMBED_MODEL.name,
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


class Index:
    def __init__(self):
        from rank_bm25 import BM25Okapi

        self.chunks = [json.loads(line) for line in
                       CHUNKS.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.vectors = np.load(VECTORS)
        with (INDEX_DIR / "bm25.pkl").open("rb") as f:
            self.bm25 = BM25Okapi(pickle.load(f))
        self.meta = json.loads(META.read_text(encoding="utf-8"))

    def dense(self, query: str, k: int = 20) -> list[tuple[int, float]]:
        sims = self.vectors @ encode_query(query)
        top = np.argpartition(-sims, k)[:k]
        return [(int(i), float(sims[i])) for i in top[np.argsort(-sims[top])]]

    def lexical(self, query: str, k: int = 20) -> list[tuple[int, float]]:
        scores = self.bm25.get_scores(tokenize(query))
        top = np.argpartition(-scores, k)[:k]
        return [(int(i), float(scores[i])) for i in top[np.argsort(-scores[top])]]

    def search(self, query: str, k: int = 5, candidate: int = 20) -> list[dict]:
        """两路召回做 RRF 融合（k=60 为论文默认值），只按名次合并不按分数。"""
        rrf: dict[int, float] = {}
        for hits in (self.dense(query, candidate), self.lexical(query, candidate)):
            for rank, (i, _) in enumerate(hits, start=1):
                rrf[i] = rrf.get(i, 0.0) + 1.0 / (60 + rank)
        out = []
        for i, score in sorted(rrf.items(), key=lambda kv: -kv[1])[:k]:
            out.append({**self.chunks[i], "score": round(score, 5)})
        return out
