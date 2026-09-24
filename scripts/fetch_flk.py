"""从「国家法律法规数据库」(flk.npc.gov.cn) 取法律全文并转成语料。

ModelScope 上几个法律数据集都是同一批 176–179 部，缺《个人信息保护法》《劳动合同法》等，
这里按标题精确检索 → 下载官方 docx → 转成「一行一条」格式，与 law_*.txt 保持一致。

站点是 Vue SPA，接口从前端 bundle 里读出来的：
    POST /law-search/search/list           {searchContent, searchType:1(标题精确), pageNum, pageSize}
    GET  /law-search/download/mobile       ?format=docx&bbbs=<版本标识>   → 302 到 OSS 签名直链
站点属政府公开数据，无许可证字段，manifest 里按「国家法律法规数据库公开文本」记录。

用法：
    python scripts/fetch_flk.py 个人信息保护法 劳动合同法
"""
from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CACHE = Path("E:/DevEnv/dataset-cache/flk-npc")
BASE = "https://flk.npc.gov.cn"
LICENSE = "国家法律法规数据库公开文本"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": BASE + "/search",
    "Content-Type": "application/json",
}

SEARCH_BODY = {
    "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 1, "sxx": [],
    "gbrqYear": [], "flfgCodeId": [], "zdjgCodeId": [],
    "orderByParam": {"order": "-1", "sort": ""},
}
# 条号：第七十四条 / 第158条；章标题（第一章）与目录行不参与切分
ARTICLE_RE = re.compile(r"^第([零〇一二三四五六七八九十百千0-9]+)条")
CHAPTER_RE = re.compile(r"^第[零〇一二三四五六七八九十百千0-9]+章")


def clean(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\xa0", " ").replace("　", " ")).strip()


def search(title: str) -> dict:
    """按标题精确检索，返回最新公布的那一条记录。"""
    r = requests.post(f"{BASE}/law-search/search/list", headers=HEADERS, timeout=45,
                      data=json.dumps({**SEARCH_BODY, "searchContent": title,
                                       "pageNum": 1, "pageSize": 20}))
    rows = r.json().get("rows") or []
    hit = [x for x in rows if clean(re.sub(r"<[^>]+>", "", x["title"])) == f"中华人民共和国{title}"]
    if not hit:
        hit = [x for x in rows if title in re.sub(r"<[^>]+>", "", x["title"])]
    if not hit:
        raise SystemExit(f"检索不到《{title}》，接口返回 {len(rows)} 条")
    return sorted(hit, key=lambda x: x["gbrq"])[-1]


def download_docx(bbbs: str, name: str) -> bytes:
    cache = CACHE / f"{name}.docx"
    if cache.exists() and cache.stat().st_size > 1024:
        print(f"  cached {cache.name} {cache.stat().st_size // 1024} KB")
        return cache.read_bytes()
    r = requests.get(f"{BASE}/law-search/download/mobile",
                     headers={**HEADERS, "Content-Type": ""}, timeout=90,
                     params={"format": "docx", "bbbs": bbbs, "fileId": ""}, allow_redirects=True)
    r.raise_for_status()
    if not r.content[:2] == b"PK":
        raise SystemExit(f"下载的不是 docx（{len(r.content)} 字节，开头 {r.content[:20]!r}）")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(r.content)
    print(f"  saved  {cache.name} {len(r.content) // 1024} KB")
    return r.content


def to_articles(data: bytes, title: str) -> list[str]:
    """docx → 「《法律名》第X条规定，……」一行一条；跨段落的同条内容合并。"""
    from docx import Document

    doc = Document(io.BytesIO(data))
    lines: list[str] = []
    for p in doc.paragraphs:
        t = re.sub(r"\s+", " ", clean(p.text))
        if not t or CHAPTER_RE.match(t) or t in title:
            continue
        if ARTICLE_RE.match(t):
            lines.append(t)
        elif lines:
            lines[-1] += t          # 条文续行（列表项如「（一）…」常独立成段）
    return [f"《{title}》{ln}" for ln in lines]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="+", help="法律名（不带「中华人民共和国」前缀）")
    args = ap.parse_args()

    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    for title in args.titles:
        full = f"中华人民共和国{title}"
        print(f"\n[{title}] 检索 flk.npc.gov.cn")
        rec = search(title)
        print(f"  命中 {rec['title']} 公布 {rec['gbrq']} 施行 {rec['sxrq']} {rec['zdjgName']}")
        data = download_docx(rec["bbbs"], title)
        arts = to_articles(data, full)
        doc_id = f"flk_{len([k for k in manifest if k.startswith('flk_')]) + 1:02d}"
        body = "\n".join(arts)
        (RAW / f"{doc_id}.txt").write_text(
            f"《{full}》\n来源：{BASE}/detail?bbbs={rec['bbbs']}\n"
            f"许可证：{LICENSE}\n\n{body}", encoding="utf-8")
        manifest[doc_id] = {
            "title": f"《{full}》", "kind": "law",
            "url": f"{BASE}/detail?bbbs={rec['bbbs']}",
            "source": "国家法律法规数据库 flk.npc.gov.cn",
            "license": LICENSE, "note": f"公布 {rec['gbrq']}，施行 {rec['sxrq']}",
            "pubtime": rec["gbrq"], "query": title,
            "articles": len(arts), "chars": len(body), "paras": len(arts),
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        print(f"  → {doc_id}.txt {len(arts)} 条 {len(body)} 字")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nmanifest 共 {len(manifest)} 篇")


if __name__ == "__main__":
    main()
