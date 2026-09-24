"""M2 检索对比：稠密 / BM25 / RRF 混合三路各自 Top-3，看各自擅长什么。

用法：
    python scripts/search_demo.py "试用期最长可以约定多久"
    python scripts/search_demo.py            # 跑内置的 6 条测试问题
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.index import Index                    # noqa: E402

DEFAULT_QUERIES = [
    "劳动合同试用期最长能约定多久",
    "个人信息跨境提供需要评估吗",
    "职务发明的专利权归谁",
    "即时配送员权益保障有哪些要求",
    "碳达峰行动方案的阶段性目标是什么",
    "上市公司收购的要约义务怎么触发",
]


def show(tag: str, hits: list[tuple[int, float]], idx: Index) -> None:
    out = []
    for i, s in hits[:3]:
        c = idx.chunks[i]
        out.append(f"{c['doc_id']}#{c['offset']}({s:.3f})")
    print(f"  {tag:<8} {'  '.join(out)}")


def main() -> None:
    idx = Index()
    print(f"index: {idx.meta['chunks']} 块 / {idx.meta['dim']} 维 / {idx.meta['embed_model']}")
    queries = [sys.argv[1]] if len(sys.argv) > 1 else DEFAULT_QUERIES
    for q in queries:
        t0 = time.time()
        dense = idx.dense(q)
        lex = idx.lexical(q)
        hybrid = idx.search(q)
        print(f"\nQ: {q}")
        show("dense", dense, idx)
        show("bm25", lex, idx)
        print(f"  hybrid   " + "  ".join(f"{c['doc_id']}#{c['offset']}({c['score']:.4f})"
                                         for c in hybrid[:3]))
        print(f"  首条命中：{hybrid[0]['title']} {hybrid[0]['article']} | {hybrid[0]['text'][:60]}")
        print(f"  耗时 {time.time()-t0:.2f}s（含模型 warm 后）")


if __name__ == "__main__":
    main()
