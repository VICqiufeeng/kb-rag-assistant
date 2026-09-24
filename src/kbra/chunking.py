"""文档切块：法条一行一条，政策文件按自然段打包到约 350 字。

切块粒度依据语料自身的结构边界（法条 / 自然段），不按固定字数硬切，
避免把一条完整规定切成两半导致检索命中后模型读不到前提条件。
超过 MAX_CHARS 的块在句子边界（。；）处再切。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import MANIFEST, RAW_DIR

ARTICLE_RE = re.compile(r"(?=第[零〇一二三四五六七八九十百千0-9]+条)")
# 行首即条号：「第十九条【试用期】…」或「《民法典》第八条规定，…」
LINE_ARTICLE_RE = re.compile(r"^(?:《[^》]{2,40}》)?第[零〇一二三四五六七八九十百千0-9]+条")
ARTICLE_IN_HEAD_RE = re.compile(r"第[零〇一二三四五六七八九十百千0-9]+条")
MAX_CHARS = 600
TARGET_CHARS = 350


def split_articles(body: str) -> list[str]:
    """法条文档：入库时每行正好是一条法条（见 scripts/import_chinese_laws.py）。
    只有行首不是条号的行才回退到正则切分——不能用「第X条」在行内出现的位置切，
    否则条内引用（如「依照本法第二百三十四条的规定」）会把一条撕成两半。"""
    pieces: list[str] = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        pieces.extend([line] if LINE_ARTICLE_RE.match(line) else split_articles_regex(line))

    merged: list[str] = []
    for p in pieces:
        if len(p) <= MAX_CHARS:
            merged.append(p)
            continue
        buf = ""
        for s in re.split(r"(?<=[。；])", p):
            if buf and len(buf) + len(s) > MAX_CHARS:
                merged.append(buf.strip())
                buf = s
            else:
                buf += s
        if buf.strip():
            merged.append(buf.strip())
    return merged


def split_articles_regex(body: str) -> list[str]:
    return [p.strip() for p in ARTICLE_RE.split(body) if p.strip()]


def split_paragraphs(body: str) -> list[str]:
    """政策文件：自然段打包到 TARGET_CHARS，句子边界处截断。"""
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > MAX_CHARS:
            if buf:
                out.append(buf)
                buf = ""
            sents = re.split(r"(?<=[。；])", p)
            for s in sents:
                if buf and len(buf) + len(s) > TARGET_CHARS:
                    out.append(buf.strip())
                    buf = s
                else:
                    buf += s
            continue
        if buf and len(buf) + len(p) > TARGET_CHARS:
            out.append(buf.strip())
            buf = p
        else:
            buf = (buf + "\n" + p).strip()
    if buf.strip():
        out.append(buf.strip())
    return out


def parse_doc(path: Path) -> tuple[str, str, str, str]:
    """返回 (标题, 来源, 许可证, 正文)。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    title = lines[0].strip()
    source = license_ = ""
    body_start = 1
    for i, line in enumerate(lines[1:6], start=1):
        if line.startswith("来源："):
            source = line[len("来源："):].strip()
        elif line.startswith("许可证："):
            license_ = line[len("许可证："):].strip()
        elif not line.strip():
            body_start = i + 1
            break
    return title, source, license_, "\n".join(lines[body_start:]).strip()


def build_chunks(doc_id: str, kind: str, title: str, source: str, license_: str, body: str) -> list[dict]:
    pieces = split_articles(body) if kind == "law" else split_paragraphs(body)

    chunks = []
    for i, text in enumerate(pieces):
        m = ARTICLE_IN_HEAD_RE.search(text[:40])
        # 检索时带上文档标题：单条「第六条…」在几百部法律里指代不明；
        # 但法条全文数据集已把《法律名》写进行内，再拼一次只会浪费 token
        embed_text = text if title and title in text else f"{title}：{text}"
        chunks.append({
            "chunk_id": f"{doc_id}#{i}",
            "doc_id": doc_id,
            "title": title,
            "url": source,
            "license": license_,
            "offset": i,
            "article": m.group(0) if m else "",
            "text": text,
            "embed_text": embed_text,
        })
    return chunks


def iter_corpus() -> list[dict]:
    """遍历 data/raw/*.txt；文件名去 .txt 即 doc_id，kind 取自 manifest。"""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    all_chunks: list[dict] = []
    for path in sorted(RAW_DIR.glob("*.txt")):
        doc_id = path.stem
        title, source, license_, body = parse_doc(path)
        kind = manifest.get(doc_id, {}).get("kind", "policy")
        all_chunks.extend(build_chunks(doc_id, kind, title, source, license_, body))
    return all_chunks


if __name__ == "__main__":
    cs = iter_corpus()
    print(f"chunks={len(cs)} docs={len({c['doc_id'] for c in cs})} "
          f"avg={sum(len(c['text']) for c in cs)//len(cs)}字 "
          f"max={max(len(c['text']) for c in cs)}字")
    from collections import Counter
    print("top doc chunk counts:", Counter(c["doc_id"] for c in cs).most_common(5))
