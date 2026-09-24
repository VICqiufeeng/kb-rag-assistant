"""M3 问答入口：检索 → 本地 3B 生成 → 引用溯源 / 拒答。

用法（项目根目录，显存需约 6GB）：
    python scripts/ask.py "劳动合同试用期最长能约定多久"
    python scripts/ask.py            # 跑内置 4 个问题（含 1 个语料外的拒答测试）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.generate import answer, load_llm, version_caveat   # noqa: E402
from kbra.index import Index                                 # noqa: E402

DEFAULT_QUERIES = [
    "劳动合同试用期最长能约定多久",
    "关键信息基础设施的个人信息要存在哪里",
    "个人数据出境需要安全评估吗",
    "2026 年世界杯冠军是谁",          # 语料外问题，应触发拒答
]


def ask(idx: Index, llm: tuple, q: str) -> None:
    r = answer(q, idx, llm=llm)
    print(f"\nQ: {q}")
    print(f"A: {r['answer']}")
    if r["citations"]:
        for c in r["citations"]:
            print(f"  [{c['n']}] {c['title']} {c['article']}  ({c['url']})")
    else:
        print("  ⚠️ 答案未带任何 [n] 引用标")
    print("  入模资料：" + " | ".join(
        f"[{i + 1}]{c['title']} {c['article']}" for i, c in enumerate(r["retrieved"])))
    if r["suspicious_citations"]:
        print("  ⚠️ 凭空引用（答案提到但未被检索到）：" + "、".join(r["suspicious_citations"]))
    for m in r.get("citation_mismatches", []):
        print(f"  ⚠️ 引用错配：{m}")
    print(f"  拒答={r['refused']} | top1相似度={r['stats']['top1_sim']}"
          f" | 生成 {r['stats'].get('new_tokens', 0)} tok / {r['stats'].get('generate_s', 0)}s"
          f" = {r['stats'].get('tok_per_s', 0)} tok/s")
    caveat = version_caveat(r["retrieved"])
    if caveat:
        print(f"  {caveat}")


def main() -> None:
    t0 = time.time()
    idx = Index()
    print(f"index: {idx.meta['chunks']} 块 / {idx.meta['dim']} 维，加载 {time.time()-t0:.1f}s")
    queries = sys.argv[1:] or DEFAULT_QUERIES
    t0 = time.time()
    llm = load_llm()
    print(f"LLM 加载 {time.time()-t0:.1f}s")
    for q in queries:
        ask(idx, llm, q)


if __name__ == "__main__":
    main()
