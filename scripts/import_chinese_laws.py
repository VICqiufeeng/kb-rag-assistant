"""导入 ModelScope `dengcao/Chinese-Laws`：177 部现行法律全文，一行一条。

原始包缓存在 E:/DevEnv/dataset-cache/chinese-laws/（git clone --depth 1 --filter=blob:none
   https://www.modelscope.cn/datasets/dengcao/Chinese-Laws.git）

注意数据集自述「数据截止至 2025 年 1 月 1 日」，法条可能不是最新版本，
这一点会写进 manifest 与 README，问答时也要提示用户核对现行有效版本。

用法：
    python scripts/import_chinese_laws.py            # 全量导入（覆盖 law_* 旧文档）
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE_ZIP = Path("E:/DevEnv/dataset-cache/chinese-laws/Chinese-Laws.zip")
DATASET = "dengcao/Chinese-Laws"
DATASET_PAGE = f"https://www.modelscope.cn/datasets/{DATASET}"
LICENSE = "Apache License 2.0"
CUTOFF = "数据截止 2025-01-01"

TITLE_RE = re.compile(r"^《(.+?)》")


def clean(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\xa0", " ")).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-sample", action="store_true",
                    help="保留之前导入的 1000 条抽样法条（默认删除，避免与全文重复）")
    args = ap.parse_args()

    if not CACHE_ZIP.exists():
        raise SystemExit(f"缺少 {CACHE_ZIP}，先 clone 数据集仓库（见本文件 docstring）")

    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    if not args.keep_sample:
        for k in [k for k, v in manifest.items() if v.get("kind") == "law"]:
            manifest.pop(k)
        for p in RAW.glob("law_*.txt"):
            p.unlink()

    added = articles = total_chars = 0
    with zipfile.ZipFile(CACHE_ZIP) as z:
        names = [n for n in sorted(z.namelist()) if n.endswith(".txt")]
        for seq, name in enumerate(names, start=1):
            lines = [clean(l) for l in z.read(name).decode("utf-8").splitlines() if clean(l)]
            body = "\n".join(lines)
            head = TITLE_RE.match(lines[0]) if lines else None
            title = head.group(1) if head else name[:-4]
            doc_id = f"law_{seq:03d}"
            (RAW / f"{doc_id}.txt").write_text(
                f"《{title}》\n来源：{DATASET_PAGE}\n许可证：{LICENSE}\n\n{body}", encoding="utf-8")
            manifest[doc_id] = {
                "title": f"《{title}》",
                "kind": "law",
                "url": DATASET_PAGE,
                "source": "modelscope:" + DATASET,
                "license": LICENSE,
                "note": CUTOFF,
                "pubtime": "",
                "query": "法律全文",
                "articles": len(lines),
                "chars": len(body),
                "paras": len(lines),
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            added += 1
            articles += len(lines)
            total_chars += len(body)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"laws={added} articles={articles} chars={total_chars} manifest={len(manifest)}")


if __name__ == "__main__":
    main()
