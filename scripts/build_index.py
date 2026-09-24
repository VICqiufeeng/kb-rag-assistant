"""M1/M2 建索引：切块 → bge 向量 → jieba+BM25 词法索引，全部落盘。

用法（在项目根目录）：
    PYTHONPATH=src python scripts/build_index.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.chunking import iter_corpus          # noqa: E402
from kbra.index import build                   # noqa: E402

chunks = iter_corpus()
print(f"docs→chunks: {len({c['doc_id'] for c in chunks})} 篇 → {len(chunks)} 块")
t0 = time.time()
meta = build(chunks)
print(f"{meta}  用时 {time.time()-t0:.1f}s")
