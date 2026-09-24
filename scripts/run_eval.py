"""M4 评测入口：检索层 Recall@k / MRR 与生成层幻觉率 / 拒答率。

    python scripts/run_eval.py                    # 只跑检索（三种召回配置 A/B）
    python scripts/run_eval.py --gen 40           # 再用本地 3B 跑 40 题生成层指标
    python scripts/run_eval.py --k 10             # 改 Recall 截断
产出打印到终端，加 --out eval/retrieval.json 落盘。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.config import EVAL_SET                       # noqa: E402
from kbra.evaluate import evaluate_retrieval, resolve_gold, summarize   # noqa: E402
from kbra.index import Index                           # noqa: E402


def retrievers(idx: Index):
    """三种召回配置：稠密、词法、混合（RRF）。README 里那张 A/B 表由此产出。"""
    def dense(q, k, _mode=None):
        return [idx.chunks[i]["chunk_id"] for i, _ in idx.dense(q, k)]

    def lexical(q, k, _mode=None):
        return [idx.chunks[i]["chunk_id"] for i, _ in idx.lexical(q, k)]

    def hybrid(q, k, _mode=None):
        return [c["chunk_id"] for c in idx.search(q, k=k, candidate=40)]

    def hybrid_c20(q, k, _mode=None):
        return [c["chunk_id"] for c in idx.search(q, k=k, candidate=20)]

    def hybrid_rerank(q, k, _mode=None):
        """混合召回 20 条候选 → 交叉编码器重排 → 截断 k。"""
        from kbra.rerank import rerank

        pool = idx.search(q, k=20, candidate=40)
        return [c["chunk_id"] for c in rerank(q, pool, top_n=k)]

    return {"dense": dense, "bm25": lexical, "hybrid(cand=40)": hybrid,
            "hybrid(cand=20)": hybrid_c20, "hybrid+rerank": hybrid_rerank}


def generation_eval(idx: Index, items: list[dict], resolved: dict, n: int) -> dict:
    """在正例里等距抽 n 题跑完整链路，另加全部负例，统计生成层指标。"""
    from kbra.generate import answer, load_llm

    pos = [it for it in items if not it.get("negative") and resolved[it["id"]]]
    step = max(1, len(pos) // n)
    sample = pos[::step][:n]
    negs = [it for it in items if it.get("negative")]

    t0 = time.time()
    llm = load_llm()
    print(f"LLM 加载 {time.time()-t0:.1f}s，生成层抽样 {len(sample)} 题 + 负例 {len(negs)} 题")

    rows = []
    for it in sample + negs:
        r = answer(it["question"], idx, llm=llm, k=4)
        gold = set(resolved[it["id"]])
        cited = {c["chunk_id"] for c in r["citations"]}
        row = {
            "id": it["id"], "question": it["question"], "negative": bool(it.get("negative")),
            "refused": r["refused"],
            "cited": bool(cited),
            # 引用的资料里至少有一条是标注的 gold 条文（另一条可能只是相邻的相关条文）
            "citation_in_gold": bool(cited & gold),
            "retrieval_hit@4": bool(gold & {c["chunk_id"] for c in r["retrieved"]}),
            "suspicious": r["suspicious_citations"], "mismatches": r["citation_mismatches"],
            "tok_per_s": r["stats"].get("tok_per_s"),
        }
        rows.append(row)
        print(f"  {row['id']:<5} 拒答={row['refused']!s:<5} 带引用={row['cited']!s:<5} "
              f"引用命中gold={row['citation_in_gold']!s:<5} 凭空法名={len(row['suspicious'])} "
              f"条号错配={len(row['mismatches'])}")

    def rate(rows_: list[dict], key: str) -> float:
        return round(sum(1 for x in rows_ if x[key]) / len(rows_), 4) if rows_ else 0.0

    gen = [x for x in rows if not x["negative"]]
    neg = [x for x in rows if x["negative"]]
    return {
        "sampled": len(gen), "negatives": len(neg),
        "answer_with_citation": rate(gen, "cited"),
        "citation_in_gold": rate([x for x in gen if x["cited"]], "citation_in_gold"),
        "hallucinated_law": rate(gen, "suspicious"),
        "citation_mismatch": rate(gen, "mismatches"),
        "retrieval_hit@4": rate(gen, "retrieval_hit@4"),
        "correct_refusal": rate(neg, "refused"),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--gen", type=int, default=0, help=">0 时额外跑生成层评测，值为抽样题数")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    items = [json.loads(l) for l in
             EVAL_SET.read_text(encoding="utf-8").splitlines() if l.strip()]
    idx = Index()
    resolved, warns = resolve_gold(items, idx.chunks)
    if warns:
        print("!! gold 标注解析失败：", *warns, sep="\n  ")

    t0 = time.time()
    rows = []
    for mode, fn in retrievers(idx).items():
        rows.append(evaluate_retrieval(items, resolved, fn, k=args.k, mode=mode))
    print(f"\n检索层评测（{len(rows[0]['misses'])} 条未命中示例见 detail），耗时 {time.time()-t0:.1f}s")
    print(summarize(rows))

    out = {"index": idx.meta, "k": args.k, "retrieval": rows}
    if args.gen:
        out["generation"] = generation_eval(idx, items, resolved, args.gen)
        g = out["generation"]
        print("\n生成层指标（本地 Qwen2.5-3B-Instruct，k=4）：")
        for key in ("sampled", "negatives", "answer_with_citation", "citation_in_gold",
                    "hallucinated_law", "citation_mismatch", "retrieval_hit@4", "correct_refusal"):
            print(f"  {key:<22}{g[key]}")

    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n落盘 {args.out}")


if __name__ == "__main__":
    main()
