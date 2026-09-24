"""M3 生成：Top-k 资料拼 Prompt → Qwen2.5-3B-Instruct → 引用溯源 / 拒答。

三道防线避免「编造」：
1. 检索首条相似度过低时直接返回拒答，不调模型（省一次本地推理，实测一次推理 1.3–3.8s）；
2. Prompt 里硬性要求：只依据资料作答，资料不足输出固定标记【无法回答】；
3. 生成后按标记判定，并把答案里的 [n] 反查成文档标题/条号/链接，无引用则标注未溯源；
4. 事后核对「法律名 ↔ 资料编号 ↔ 条号」三者是否一致，不一致就打印 ⚠️（见 check_citations）。
"""
from __future__ import annotations

import re
import time
from typing import Any

from .config import LLM_MODEL

REFUSAL_MARK = "【无法回答】"
# 首条稠密相似度低于此值就认为语料里没有相关内容
MIN_SIM = 0.30

SYSTEM = (
    "你是企业知识库问答助手。严格遵守以下规则：\n"
    "1. 只依据【资料】作答，不得使用资料之外的知识，不得推测；\n"
    "2. 每条结论后标注支撑它的资料编号，如 [1] 或 [2][3]；\n"
    f"3. 资料不足以回答时，只输出一行：{REFUSAL_MARK}，并用一句话说明缺少什么，不要给出猜测性答案；\n"
    "4. 用简体中文回答，不超过 200 字。"
)

CITED_RE = re.compile(r"\[(\d{1,2})\]")
MENTIONED_RE = re.compile(r"《[^》]{2,30}》")
# 法律名后紧跟的 [n] 编号，用于核对「名」与「号」是否指向同一份资料
PAIR_RE = re.compile(r"《([^》]{2,30})》[^。\[]{0,15}?\[(\d{1,2})\]")
# 答案里写成「《X》第Y条」的条号，X 在资料中却没这条 → 条号张冠李戴
_ART_NUM = r"第[零〇一二三四五六七八九十百千0-9]+条"
ARTICLE_NUM_RE = re.compile(_ART_NUM)
# 只认紧贴在法名后的条号串：「第三十条和第三十七条」「第十二条、第十三条」。
# 中间隔着动词/文字的（如「依照本法第四十条」）是法条交叉引用，属正确内容。
ATTACHED_ARTICLES_RE = re.compile(rf"{_ART_NUM}(?:(?:、|和|与|及|，){_ART_NUM}?)*")
# 3B 模型偶尔把 Prompt 的段头连同引用标一起复读成答案开头（M5 接口冒烟实测到：
# 「【资料】[1][2]\n根据《中华人民共和国劳动合同法》…」）。只清显示文本，
# 引用解析仍在原文上做——否则这行的 [1][2] 会从溯源结果里被丢掉。
ECHO_RE = re.compile(r"^【资料】[^\n]*\n+")

CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def article_no(art: str) -> int:
    """条号归一：模型常把「第三十五条」写成「第35条」，按字面比会误判成错配。"""
    body = art.strip("第条 ")
    if body.isdigit():
        return int(body)
    total = cur = 0
    for ch in body:
        if ch in CN_DIGIT:
            cur = cur * 10 + CN_DIGIT[ch]
        else:
            total += (cur or 1) * CN_UNIT[ch]
            cur = 0
    return total + cur


def load_llm():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not LLM_MODEL.exists():
        raise SystemExit(f"缺少模型 {LLM_MODEL}，先跑 scripts/fetch_model.py --ms Qwen/Qwen2.5-3B-Instruct")
    tok = AutoTokenizer.from_pretrained(LLM_MODEL)
    model = AutoModelForCausalLM.from_pretrained(LLM_MODEL, dtype=torch.float16, device_map="cuda")
    return tok, model


def build_prompt(question: str, chunks: list[dict]) -> str:
    mats = "\n\n".join(
        f"[{i + 1}] {c['title']} {c['article']}\n{c['text']}" for i, c in enumerate(chunks)
    )
    return f"【资料】\n{mats}\n\n【问题】{question}"


def answer(question: str, idx, llm: tuple | None = None, k: int = 4,
           max_new_tokens: int = 260) -> dict[str, Any]:
    """返回 {answer, refused, citations, retrieved, stats}。"""
    t0 = time.perf_counter()
    best = idx.dense(question, 1)[0][1]
    chunks = idx.search(question, k=k)
    t_retrieve = time.perf_counter() - t0

    if best < MIN_SIM:
        return {
            "answer": f"{REFUSAL_MARK}知识库中未检索到与该问题相关的内容（最高相似度 {best:.2f}）。",
            "refused": True, "citations": [], "suspicious_citations": [],
            "citation_mismatches": [],
            "retrieved": chunks,
            "stats": {"top1_sim": round(best, 3), "reason": "低相似度，未调用模型",
                      "retrieve_s": round(t_retrieve, 3)},
        }

    tok, model = llm if llm else load_llm()
    prompt = build_prompt(question, chunks)
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = tok(text, return_tensors="pt").to(model.device)

    import torch

    t1 = time.perf_counter()
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    new = out[0][inputs["input_ids"].shape[1]:]
    dt = time.perf_counter() - t1
    raw = tok.decode(new, skip_special_tokens=True).strip()

    cited = sorted({int(n) for n in CITED_RE.findall(raw)} - {0})
    citations = [{
        "n": n, "chunk_id": chunks[n - 1]["chunk_id"], "doc_id": chunks[n - 1]["doc_id"],
        "title": chunks[n - 1]["title"],
        "article": chunks[n - 1]["article"], "url": chunks[n - 1]["url"],
        "license": chunks[n - 1]["license"],
    } for n in cited if n <= len(chunks)]

    refused = raw.lstrip().startswith(REFUSAL_MARK)
    # 拒答的答案不必再核引用：它会提到语料里没有的名字（如「红楼梦」），那是叙述不是引用。
    suspicious, mismatched = ([], []) if refused else check_citations(raw, chunks)

    return {
        "answer": ECHO_RE.sub("", raw, count=1).strip(),
        "refused": refused,
        "citations": citations,
        "suspicious_citations": suspicious,
        "citation_mismatches": mismatched,
        "retrieved": chunks,
        "stats": {
            "top1_sim": round(best, 3),
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "new_tokens": int(len(new)),
            "retrieve_s": round(t_retrieve, 3),
            "generate_s": round(dt, 2),
            "tok_per_s": round(len(new) / dt, 2) if dt else 0,
        },
    }


def check_citations(raw: str, chunks: list[dict]) -> tuple[list[str], list[str]]:
    """引用一致性校验，返回 (凭空引用的法律, 引用错配说明)。三类错误分开判：

    - suspicious：答案提到、但本次检索里根本没有这部法律 → 可能是凭空引用；
    - mismatched：法律名在资料里，却挂到了别的资料编号上（3B 模型很常见的张冠李戴）；
    - 条号错配：答案写「《X》第Y条」而资料里 X 名下没有第Y条。前一种按名↔编号配，
      只在法名与 [n] 同句时才生效，这一条补上跨句的情况。
    """
    # 同一部法律可能在资料里占多个编号（不同条），所以是「名 → 编号列表」
    slots: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        slots.setdefault(c["title"], []).append(i + 1)
    suspicious = sorted(m for m in set(MENTIONED_RE.findall(raw)) if m not in slots)

    mismatched: list[str] = []
    for name, num in PAIR_RE.findall(raw):
        want = slots.get(f"《{name}》")
        if want and int(num) not in want:
            mismatched.append(f"《{name}》标为[{num}]，资料中实为{''.join(f'[{n}]' for n in want)}")

    articles: dict[str, set[int]] = {}
    for c in chunks:
        if c["article"]:
            articles.setdefault(c["title"], set()).add(article_no(c["article"]))
    for m in MENTIONED_RE.finditer(raw):
        name = m.group()
        if name not in articles:
            continue
        # 只认紧贴在法名后面的一串条号（如「第三十条和第三十七条」）。
        # 隔了标点/动词的条号常是法条内部的交叉引用（「依照本法第四十条」），
        # 那是正确内容，不能当错配报。
        run = ATTACHED_ARTICLES_RE.match(raw[m.end():])
        if not run:
            continue
        for art in ARTICLE_NUM_RE.findall(run.group()):
            if article_no(art) not in articles[name]:
                have = "、".join(f"第{n}条" for n in sorted(articles[name]))
                mismatched.append(f"{name}被引作{art}，本次资料里该法只有 {have}")
    return suspicious, mismatched


def version_caveat(chunks: list[dict]) -> str:
    """法条全文数据集自述截止 2025-01-01，回答涉及时必须提示核对现行版本。"""
    if any(c["url"].startswith("https://www.modelscope.cn/datasets/dengcao") for c in chunks):
        return "提示：所引法条来自「数据截止 2025-01-01」的数据集，请核对现行有效版本。"
    return ""
