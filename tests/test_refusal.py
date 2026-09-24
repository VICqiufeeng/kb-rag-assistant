"""低相似度短路这条拒答路径的单测（纯逻辑，不需要 GPU 与 MySQL）。

起因：M5 冒烟时把「2026 年世界杯冠军是谁」记成「低相似度短路、未调用模型」，
实测该问 top1_sim=0.406 > MIN_SIM=0.30，走的是**模型自述【无法回答】**那条路。
两条路的开销与 stats 字段不一样，所以把容易和它混淆的短路单独锁住。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kbra.generate import MIN_SIM, REFUSAL_MARK, answer   # noqa: E402

CHUNK = {"chunk_id": "law_056#30", "doc_id": "law_056",
         "title": "《中华人民共和国数据安全法》", "article": "第三十条",
         "text": "第三十条 重要数据的处理者应当…", "url": "https://example", "license": "Apache-2.0"}


class LowSimIndex:
    """首条相似度低于 MIN_SIM 的假索引。"""

    def __init__(self, score: float = 0.287) -> None:
        self.score = score

    def dense(self, q, k=1):
        return [(0, self.score)]

    def search(self, q, k=4, candidate=20):
        return [dict(CHUNK)]


def test_低相似度时在调用模型前就拒答():
    """llm 传一个会立刻抛异常的哨兵：真走到模型就炸，不会静默通过。"""
    res = answer("随便一个语料外的问题", LowSimIndex(), llm=(object(), object()))
    assert res["refused"] is True
    assert res["answer"].startswith(REFUSAL_MARK)
    assert res["citations"] == []
    assert res["stats"]["reason"] == "低相似度，未调用模型"
    assert "generate_s" not in res["stats"]        # 没调模型就不该有生成耗时
    assert res["stats"]["top1_sim"] == 0.287
    assert len(res["retrieved"]) == 1              # 候选仍返回，前端调试面板要看


def test_相似度刚好等于阈值时不放行():
    """判定是 best < MIN_SIM，等于阈值要走模型（这里同样用哨兵证明没放行）。"""
    import pytest
    with pytest.raises(Exception):
        answer("边界问题", LowSimIndex(score=MIN_SIM), llm=(object(), object()))


def test_实测负例都高于阈值():
    """M5 在 MySQL 那轮实测：中文负例最低 0.406，英文/SQL 问题也只有 0.42。
    阈值若上调到 0.45，这类问题会在模型判断之前就被拦掉，拒答率数字要重测。"""
    assert 0.406 > MIN_SIM
