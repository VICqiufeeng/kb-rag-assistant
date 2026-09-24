"""出题辅助：列出指定文档里「含事实点」的条文（数字/期限/比例/金额），供人工命题用。

    python scripts/eval_seed.py law_056 flk_01 --max 12
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.config import CHUNKS   # noqa: E402

# 可考点：百分比、金额、天数、年月、序数、金额上限
FACT_RE = re.compile(r"\d+%|百分之|[\d一二三四五六七八九十百千]+(?:日|个月|年|万元|元|倍|人|名|个工作日)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_ids", nargs="+")
    ap.add_argument("--max", type=int, default=10, help="每个文档打印多少条")
    args = ap.parse_args()

    rows = [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]
    for doc_id in args.doc_ids:
        blocks = [c for c in rows if c["doc_id"] == doc_id]
        if not blocks:
            print(f"!! {doc_id} 不在索引里")
            continue
        print(f"\n===== {doc_id} {blocks[0]['title']} 共 {len(blocks)} 条 =====")
        hits = [c for c in blocks if FACT_RE.search(c["text"]) and len(c["text"]) > 40]
        for c in hits[: args.max]:
            print(f"[{c['chunk_id']}] {c['text'][:170]}")


if __name__ == "__main__":
    main()
