"""M4 评测：读 eval/qa_set.jsonl，算 Recall@k / MRR / 拒答准确率，并做检索配置 A/B。

设计要点：
- gold 用「法律名 + 条号」标注，而不是内部 chunk_id，换语料重跑也不用重标；
  校验阶段会把 gold 解析成真实 chunk_id，解析不到就说明标注写错了。
- 负例（语料里没有答案的问题）单独算：正确行为是**不检索到相关内容并拒答**。
"""
from __future__ import annotations

import json
import re
from typing import Any

GOLD_RE = re.compile(r"^《(.+?)》$")


def resolve_gold(items: list[dict], chunks: list[dict]) -> tuple[dict, list[str]]:
    """把 {title, article} 标注映射成 chunk_id。

    article 为空表示政策文件（无条号），按标题匹配整篇。
    返回 {qid: [chunk_id,...]} 和无法解析的告警列表。
    """
    by_key: dict[tuple[str, str], list[str]] = {}
    for c in chunks:
        title = GOLD_RE.sub(r"\1", c["title"])
        by_key.setdefault((title, c["article"]), []).append(c["chunk_id"])

    resolved, warns = {}, []
    for it in items:
        ids: list[str] = []
        for g in it.get("gold", []):
            got = by_key.get((g["title"], g.get("article", "")), [])
            if not got:
                warns.append(f"{it['id']}: 标注不到条目 《{g['title']}》{g.get('article','')}")
            ids += got
        resolved[it["id"]] = sorted(set(ids))
    return resolved, warns


def rank_metrics(ranked: list[str], gold: set[str]) -> tuple[int, float]:
    """返回 (hit@k, reciprocal rank)。gold 为空表示负例，不参与检索打分。"""
    for rank, cid in enumerate(ranked, start=1):
        if cid in gold:
            return 1, 1.0 / rank
    return 0, 0.0


def evaluate_retrieval(items: list[dict], resolved: dict, retrieve, k: int = 5,
                       mode: str = "hybrid") -> dict[str, Any]:
    """retrieve(query, k, mode) -> list[chunk_id]。按正例统计 Recall@k / MRR。"""
    pos = [it for it in items if not it.get("negative")]
    hits = rrs = 0
    misses = []
    for it in pos:
        gold = set(resolved[it["id"]])
        if not gold:                      # 标注解析失败的题不计入分母
            continue
        ranked = retrieve(it["question"], k, mode)
        hit, rr = rank_metrics(ranked, gold)
        hits += hit
        rrs += rr
        if not hit:
            misses.append({"id": it["id"], "question": it["question"],
                           "gold": sorted(gold)[:3], "top1": ranked[0] if ranked else ""})
    n = sum(1 for it in pos if resolved[it["id"]])
    return {"mode": mode, "k": k, "n": n, f"recall@{k}": round(hits / n, 4) if n else 0.0,
            "mrr": round(rrs / n, 4) if n else 0.0, "misses": misses}


def summarize(rows: list[dict]) -> str:
    """A/B 对比表；列名跟随实际 k（evaluate_retrieval 用 k 生成键名）。"""
    key = next(k for k in rows[0] if k.startswith("recall@"))
    head = f"{'配置':<20}{'n':>5}{key:>12}{'MRR':>9}"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(f"{r['mode']:<20}{r['n']:>5}{r[key]:>12.3f}{r['mrr']:>9.3f}")
    return "\n".join(lines)
