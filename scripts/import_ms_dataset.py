"""导入 ModelScope 公开数据集，转成 data/raw/ 语料。

默认用 Panda233/china_legal_articles_samples（Apache-2.0，1000 条现行法条）。
原始下载缓存在 E:/DevEnv/dataset-cache/（环境/缓存类目录，不入库）。

用法：
    python scripts/import_ms_dataset.py
    python scripts/import_ms_dataset.py --file other.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = Path("E:/DevEnv/dataset-cache/Panda233/china_legal_articles_samples")
DATASET = "Panda233/china_legal_articles_samples"
DATASET_PAGE = f"https://www.modelscope.cn/datasets/{DATASET}"
LICENSE = "Apache License 2.0"

CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def cn2int(s: str) -> int | None:
    """把「第四百八十三」这类中文数字转成整数；无法解析时返回 None。"""
    if s.isdigit():
        return int(s)
    total = num = 0
    for ch in s:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        elif ch in CN_UNIT:
            total += (num or 1) * CN_UNIT[ch]
            num = 0
        else:
            return None
    return total + num


def norm_title(title: str) -> str:
    """去掉《》与「(2012修正)」这类版本后缀，用于跨数据集判断是否同一部法律。"""
    t = re.sub(r"[（(][^（）()]*[修正修订废止][^（）()]*[）)]\s*$", "", title)
    return t.strip("《》 \t")


def article_key(text: str) -> tuple[int, int]:
    """按条号排序；「之一/之二」排在同条之后。"""
    m = re.match(r"第([零〇一二三四五六七八九十百千0-9]+)条(之[一二三四五六七八九十])?", text)
    if not m:
        return (10**6, 0)
    n = cn2int(m.group(1))
    if n is None:
        return (10**6, 0)
    return (n, cn2int(m.group(2)[1:]) if m.group(2) else 0)


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\xa0", " "))
    return re.sub(r"[ \t\u3000]+", " ", text).strip()


def download(name: str) -> Path:
    import requests

    target = CACHE / name
    if target.exists() and target.stat().st_size > 1024:
        print(f"cached  {target}  {target.stat().st_size/1024:.0f} KB")
        return target
    url = f"https://www.modelscope.cn/datasets/{DATASET}/resolve/master/{name}"
    print("GET", url)
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(r.content)
    print(f"saved {target}  {len(r.content)/1024:.0f} KB")
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="law_item.jsonl")
    ap.add_argument("--purge", action="store_true", help="先删除上次导入的法条文档，用于换数据集或改切法时重跑")
    args = ap.parse_args()

    path = download(args.file)
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))

    by_law: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        title = clean(r.get("title") or r.get("text") or "")
        if not title:
            continue
        by_law[title].append(r)

    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    if args.purge:
        for k in [k for k in manifest if k.startswith("law_s")]:
            manifest.pop(k)
        for p in RAW.glob("law_s*.txt"):
            p.unlink()

    # 全文数据集（dengcao/Chinese-Laws）已覆盖的法律不再重复导入，
    # 只补它缺的那部分（如劳动合同法），否则同一条法条会出现两份索引。
    full_titles = {norm_title(v["title"]) for k, v in manifest.items()
                   if v.get("kind") == "law" and not k.startswith("law_s")}

    added, total_chars, skipped = 0, 0, 0
    for idx, (title, items) in enumerate(sorted(by_law.items()), start=1):
        if norm_title(title) in full_titles:
            skipped += 1
            continue
        items.sort(key=lambda r: article_key(clean(r["contents"])))
        doc_id = f"law_s{idx:03d}"
        body = "\n".join(clean(r["contents"]) for r in items)
        text = f"{title}\n来源：{DATASET_PAGE}\n许可证：{LICENSE}\n\n{body}"
        (RAW / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        manifest[doc_id] = {
            "title": title,
            "kind": "law",
            "url": DATASET_PAGE,
            "source": "modelscope:" + DATASET,
            "license": LICENSE,
            "pubtime": "",
            "query": "法律条文",
            "articles": len(items),
            "chars": len(body),
            "paras": len(items),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        added += 1
        total_chars += len(body)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"laws={added} skipped_already_covered={skipped} articles={len(rows)} "
          f"chars={total_chars} manifest={len(manifest)}")


if __name__ == "__main__":
    main()
