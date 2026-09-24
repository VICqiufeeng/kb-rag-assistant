"""校验 eval/qa_set.jsonl：gold 标注能否解析到真实块、答案关键词是否确实在该块里。

    python scripts/validate_qa.py
标注写错（法名/条号/关键词）会逐条列出，全部通过后才适合跑评测。
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.config import CHUNKS            # noqa: E402
from kbra.evaluate import resolve_gold    # noqa: E402

items = [json.loads(l) for l in
         Path("eval/qa_set.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
chunks = [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]
by_id = {c["chunk_id"]: c for c in chunks}

resolved, warns = resolve_gold(items, chunks)

problems = list(warns)
for it in items:
    ids = resolved[it["id"]]
    if it.get("negative"):
        continue
    if not ids:
        continue                       # 解析失败已由 warns 报过
    key = it.get("answer", "")
    if key and not any(key in by_id[i]["text"] for i in ids):
        head = by_id[ids[0]]["text"][:70] if ids else "(无)"
        problems.append(f"{it['id']} 「{it['question'][:24]}」关键词「{key}」不在 gold 里：{head}")

kinds = Counter(it.get("kind") for it in items)
print(f"题目 {len(items)}（正例 {sum(1 for i in items if not i.get('negative'))} / "
      f"负例 {sum(1 for i in items if i.get('negative'))}），来源分布 {dict(kinds)}")
print(f"gold 可解析的正例：{sum(1 for i in items if not i.get('negative') and resolved[i['id']])}")
if problems:
    print(f"\n!! {len(problems)} 条问题：")
    print("\n".join(problems))
else:
    print("全部标注与关键词校验通过")
