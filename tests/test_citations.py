"""引用一致性与评测打分的单测（纯逻辑，不需要 GPU）。

用例来自真实跑出来的答案，不是编的：
- 条号错配那条是问「个人数据出境需要安全评估吗」时 3B 模型的实际输出；
- 交叉引用那条是问「个人信息跨境提供需要满足什么条件」时模型对第三十八条的正确复述。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.evaluate import rank_metrics, resolve_gold      # noqa: E402
from kbra.generate import article_no, check_citations     # noqa: E402

CHUNKS = [
    {"chunk_id": "law_056#30", "doc_id": "law_056", "title": "《中华人民共和国数据安全法》",
     "article": "第三十一条", "text": "…", "url": "u", "license": "l"},
    {"chunk_id": "law_056#34", "doc_id": "law_056", "title": "《中华人民共和国数据安全法》",
     "article": "第三十五条", "text": "…", "url": "u", "license": "l"},
    {"chunk_id": "law_132#36", "doc_id": "law_132", "title": "《中华人民共和国网络安全法》",
     "article": "第三十七条", "text": "…", "url": "u", "license": "l"},
    {"chunk_id": "law_056#29", "doc_id": "law_056", "title": "《中华人民共和国数据安全法》",
     "article": "第三十条", "text": "…", "url": "u", "license": "l"},
]


def test_凭空引用的法律名():
    raw = "依《中华人民共和国劳动法》第六十五条，劳动者应当完成劳动任务 [1]。"
    suspicious, _ = check_citations(raw, CHUNKS)
    assert suspicious == ["《中华人民共和国劳动法》"]


def test_法名与资料编号错配():
    """同一部法律占多个编号时，正确编号要全部列出来。"""
    raw = "个人信息应当境内存储，《中华人民共和国数据安全法》[3]。"
    _, mismatched = check_citations(raw, CHUNKS)
    assert mismatched == ["《中华人民共和国数据安全法》标为[3]，资料中实为[1][2][4]"]


def test_法名挂在本法的任一条编号上都不算错():
    raw = "《中华人民共和国数据安全法》[2]要求建立分类分级制度。"
    _, mismatched = check_citations(raw, CHUNKS)
    assert mismatched == []


def test_跨句的条号张冠李戴():
    """实测抓到的那条：第三十七条属网安法，模型挂到了数安法名下。"""
    raw = ("需要。根据《中华人民共和国数据安全法》第三十条和第三十七条的规定，"
           "重要数据的处理者应当向境外提供前进行安全评估 [3][4]。")
    _, mismatched = check_citations(raw, CHUNKS)
    assert any("被引作第三十七条" in m for m in mismatched)


def test_法条内部的交叉引用不误报():
    """模型正确复述个保法第三十八条时提到的「本法第四十条」不是错配。"""
    chunks = CHUNKS + [{"chunk_id": "flk_01#37", "doc_id": "flk_01",
                        "title": "《中华人民共和国个人信息保护法》", "article": "第三十八条",
                        "text": "…", "url": "u", "license": "l"}]
    raw = "条件之一：[1] 依照本法第四十条的规定通过国家网信部门组织的安全评估。"
    suspicious, mismatched = check_citations(raw, chunks)
    assert suspicious == [] and mismatched == []


def test_全对的答案不产生告警():
    raw = "《中华人民共和国数据安全法》第三十条要求开展风险评估 [4]。"
    suspicious, mismatched = check_citations(raw, CHUNKS)
    assert suspicious == [] and mismatched == []


def test_条号归一():
    assert article_no("第三十五条") == 35
    assert article_no("第35条") == 35
    assert article_no("第一千二百五十四条") == 1254
    assert article_no("第一百五十六条") == 156
    assert article_no("第十条") == 10


def test_模型用阿拉伯数字写条号不算错配():
    """实测踩过（p037）：语料写「第三十五条」，模型写「第35条」，按字面比就成了假错配。"""
    chunks = CHUNKS + [{"chunk_id": "law_132#34", "doc_id": "law_132",
                        "title": "《中华人民共和国网络安全法》", "article": "第三十五条",
                        "text": "…", "url": "u", "license": "l"}]
    raw = "未经安全审查不得使用，《中华人民共和国网络安全法》第35条 [3]。"
    _, mismatched = check_citations(raw, chunks)
    assert mismatched == []


# --- 段头回显清理 ---

ECHO = ("【资料】[1][2]\n"
        "根据《中华人民共和国劳动合同法》及其修正案，劳动合同试用期最长不得超过六个月。"
        "三年以上固定期限和无固定期限的劳动合同，试用期不得超过六个月。")


def test_清掉复读的段头但保留引用解析():
    """真实输出（M5 接口冒烟，问题=劳动合同试用期最长能约定多久）。"""
    from kbra.generate import ECHO_RE, CITED_RE

    assert ECHO_RE.sub("", ECHO, count=1).strip().startswith("根据《")
    # 引用标来自原文，清理只作用于显示文本
    assert sorted({int(n) for n in CITED_RE.findall(ECHO)}) == [1, 2]


def test_正常答案不会被误删():
    from kbra.generate import ECHO_RE

    raw = "《中华人民共和国劳动合同法》第十九条规定 [1]。"
    assert ECHO_RE.sub("", raw, count=1) == raw


def test_版本提示只在有风险的语料上出现():
    """dengcao 那份法条数据集自述截止 2025-01-01；flk 官方现行文本不需要这句提示。"""
    from kbra.generate import version_caveat

    dengcao = {"url": "https://www.modelscope.cn/datasets/dengcao/Chinese-Laws"}
    flk = {"url": "https://flk.npc.gov.cn/detail2.html?xxx"}
    assert "截止 2025-01-01" in version_caveat([dengcao, flk])
    assert version_caveat([flk]) == ""


# --- 评测打分 ---

def test_gold_标注解析成_chunk_id():
    items = [{"id": "a", "question": "q", "gold": [
        {"title": "中华人民共和国数据安全法", "article": "第三十条"}]}]
    resolved, warns = resolve_gold(items, CHUNKS)
    assert resolved["a"] == ["law_056#29"] and warns == []


def test_标注了不存在的条号会告警():
    items = [{"id": "a", "question": "q", "gold": [
        {"title": "中华人民共和国数据安全法", "article": "第九十九条"}]}]
    resolved, warns = resolve_gold(items, CHUNKS)
    assert resolved["a"] == [] and len(warns) == 1


def test_recall_与_rank_倒数():
    assert rank_metrics(["x", "gold1", "y"], {"gold1"}) == (1, 0.5)
    assert rank_metrics(["x", "y", "z"], {"gold1"}) == (0, 0.0)
