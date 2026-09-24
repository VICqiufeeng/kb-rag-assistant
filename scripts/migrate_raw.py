"""一次性迁移：统一 data/raw 的文件命名与头部格式。

背景：初版 fetch_corpus.py 用裸 id 作 manifest key、文件不带来源头部，
导致 manifest key 与文件名不一致、切块器无法从文件本身拿到来源。
本脚本只跑一次（幂等：已迁移过的条目会跳过）。
"""
import json
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
LICENSE = "中国政府网公开文件"

mf_path = RAW / "manifest.json"
mf = json.loads(mf_path.read_text(encoding="utf-8"))

# 清掉旧版法条文档（不带 kind 的 lawNNN），改由 import_ms_dataset.py 重新生成
for k in [k for k in mf if k.startswith("law") and not k.startswith("law_")]:
    mf.pop(k)
for p in RAW.glob("law[0-9][0-9][0-9].txt"):
    p.unlink()

new = {}
for k, v in mf.items():
    if k.startswith("gov_") or not k.isdigit():
        new[k] = v
        continue
    doc_id = f"gov_{k}"
    path = RAW / f"{doc_id}.txt"
    text = path.read_text(encoding="utf-8")
    if f"来源：{v['url']}" not in text[:400]:
        title = (v.get("title") or "").strip()
        path.write_text(f"{title}\n来源：{v['url']}\n许可证：{LICENSE}\n\n{text}", encoding="utf-8")
    v["kind"] = "policy"
    v["source"] = "gov.cn 政策文件库"
    v["license"] = LICENSE
    new[doc_id] = v

mf_path.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
print("manifest docs:", len(new), "| gov:", sum(1 for k in new if k.startswith("gov_")))
for k in list(new)[:2]:
    print(k, new[k]["title"][:30], "|", new[k]["url"][:60])
