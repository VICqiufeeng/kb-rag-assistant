"""交叉编码器重排（BAAI/bge-reranker-base）：对召回候选按「问题-段落」联合打分再截断。

召回（dense/BM25）是各自独立给文档建索引，问题与文档不交互；重排慢但准，
所以只对候选集（20 条）做，不对全库 1.6 万块做。
"""
from __future__ import annotations

from .config import RERANK_MODEL

_TOKENIZER = None
_MODEL = None


def get_reranker():
    global _TOKENIZER, _MODEL
    if _MODEL is None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if not RERANK_MODEL.exists():
            raise SystemExit(
                f"缺少重排模型 {RERANK_MODEL}，先跑 "
                f"scripts/fetch_model.py --ms BAAI/bge-reranker-base")
        _TOKENIZER = AutoTokenizer.from_pretrained(RERANK_MODEL)
        _MODEL = AutoModelForSequenceClassification.from_pretrained(
            RERANK_MODEL, dtype=torch.float16, device_map="cuda").eval()
    return _TOKENIZER, _MODEL


def scores(query: str, texts: list[str], batch_size: int = 8) -> list[float]:
    """返回每条文本与问题的相关度（sigmoid 后 0~1）。"""
    import torch

    tok, model = get_reranker()
    out: list[float] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            enc = tok([[query, t] for t in batch], padding=True, truncation=True,
                      max_length=512, return_tensors="pt").to(model.device)
            logits = model(**enc).logits.view(-1).float()
            out.extend(torch.sigmoid(logits).tolist())
    return out


def rerank(query: str, chunks: list[dict], top_n: int = 5) -> list[dict]:
    """按重排分降序取前 top_n，块上带 rerank_score 供调试面板展示。"""
    if not chunks:
        return []
    ss = scores(query, [c["embed_text"] for c in chunks])
    ranked = sorted(zip(ss, chunks, strict=True), key=lambda x: -x[0])
    return [{**c, "rerank_score": round(s, 4)} for s, c in ranked[:top_n]]
