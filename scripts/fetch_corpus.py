# -*- coding: utf-8 -*-
"""从国务院政策文件库抓取公开文件全文，作为 RAG 语料。

来源：https://www.gov.cn/zhengce/zhengceku/ （公开发布的国务院公文，注明出处即可使用）
产物：data/raw/gov_<id>.txt      纯正文，供入库切块
      data/raw/manifest.json    标题/文号/发布时间/原文链接，供评测题溯源

用法：
    python scripts/fetch_corpus.py --queries 数据安全 个人信息保护 人工智能 数字经济 --per-query 4
"""
import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SEARCH = "https://sousuo.www.gov.cn/search-gov/data"
HEADERS = {"User-Agent": "Mozilla/5.0"}
# 只收国务院/国务院办公厅文件：文号形如 国发〔2024〕1号 / 国办发〔2024〕2号
KEEP_PREFIX = ("国发", "国办发", "国办函")
MIN_CHARS = 800
LICENSE = "中国政府网公开文件"


def search(keyword: str, pages: int = 2):
    out = []
    for p in range(1, pages + 1):
        r = requests.get(SEARCH, params={"t": "zhengcelibrary_gw", "q": keyword, "p": p},
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        out += r.json()["searchVO"]["listVO"]
        time.sleep(0.4)
    return out


def clean(text: str) -> str:
    text = text.replace("\xa0", " ").replace("　", " ")
    text = re.sub(r"</?em>", "", text)  # 搜索接口会在标题里插 <em> 高亮
    lines = [re.sub(r" {2,}", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def fetch_body(url: str) -> tuple[str, int]:
    html = requests.get(url, headers=HEADERS, timeout=30).content
    # 注意：gov.cn 页面会让 lxml 提前截断 DOM（实测 6.6KB vs 26.8KB），必须用 html.parser
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("#UCAP-CONTENT") or soup.select_one(".pages_content") or soup.body
    paras = [p.get_text() for p in box.find_all("p")] or [box.get_text()]
    return clean("\n".join(paras)), len(box.find_all("p"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="+", default=["数据安全", "个人信息保护", "人工智能"])
    ap.add_argument("--per-query", type=int, default=4)
    ap.add_argument("--pages", type=int, default=2)
    args = ap.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    seen = set(manifest)

    for kw in args.queries:
        kept = 0
        for e in search(kw, args.pages):
            doc_id, url = "gov_" + str(e.get("id")), e.get("url") or ""
            pcode = e.get("pcode") or e.get("wenhao") or ""
            if not url.startswith("http") or doc_id in seen:
                continue
            if not pcode.startswith(KEEP_PREFIX):
                continue
            text, n_para = fetch_body(url)
            if len(text) < MIN_CHARS:
                print(f"  skip  {pcode} 正文仅 {len(text)} 字（可能在附件里）")
                seen.add(doc_id)
                continue
            title = clean(e.get("title") or "")
            header = f"{title}\n来源：{url}\n许可证：{LICENSE}\n\n"
            (RAW / f"{doc_id}.txt").write_text(header + text, encoding="utf-8")
            manifest[doc_id] = {
                "title": title, "kind": "policy", "pcode": pcode, "url": url,
                "source": "gov.cn 政策文件库", "license": LICENSE,
                "pubtime": e.get("pubtimeStr") or e.get("pubtime"),
                "query": kw, "chars": len(text), "paras": n_para,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            seen.add(doc_id)
            kept += 1
            print(f"  got   {pcode} {e.get('title')[:28]} {len(text)} 字")
            if kept >= args.per_query:
                break
            time.sleep(0.6)
        print(f"[{kw}] 本轮入库 {kept} 篇")

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(v["chars"] for v in manifest.values())
    print(f"合计 {len(manifest)} 篇 / {total/1000:.0f} 千字 -> {RAW}")


if __name__ == "__main__":
    main()
