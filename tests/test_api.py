"""M5 接口测试：注册/登录鉴权、问答落库、反馈、历史。

不依赖 GPU 与 MySQL：索引和模型走 dependency_overrides，生成函数打桩，
库用临时 sqlite（表结构与 MySQL 同一套 SQLAlchemy 元数据）。
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import kbra.api as api  # noqa: E402
from kbra.db import Base, Feedback, QaLog  # noqa: E402

CHUNK = {"chunk_id": "law_056#30", "doc_id": "law_056", "title": "《中华人民共和国数据安全法》",
         "article": "第三十条", "text": "第三十条 从事安全评估工作应当…" * 40,
         "url": "https://www.modelscope.cn/datasets/dengcao/Chinese-Laws",
         "license": "Apache-2.0", "score": 0.016}


class FakeIndex:
    def dense(self, q, k=1):
        return [(0, 0.71)]

    def search(self, q, k=4, candidate=20):
        return [dict(CHUNK)]


def stub_answer(question, idx, llm=None, k=4, **_):
    assert idx is not None and llm is not None          # 依赖注入确实把索引和模型传进来了
    return {
        "answer": "网络安全法要求 [1]。",
        "refused": False,
        "citations": [{"n": 1, "chunk_id": "law_056#30", "doc_id": "law_056",
                       "title": "《中华人民共和国数据安全法》", "article": "第三十条",
                       "url": "u", "license": "l"}],
        "suspicious_citations": ["《中华人民共和国网络安全法》"],
        "citation_mismatches": [],
        "retrieved": [dict(CHUNK)],
        "stats": {"top1_sim": 0.71, "prompt_tokens": 900, "new_tokens": 12,
                  "retrieve_s": 0.05, "generate_s": 1.5, "tok_per_s": 8.0},
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'kbra.db'}")
    Base.metadata.create_all(engine)
    sf = sessionmaker(engine, expire_on_commit=False)

    def get_db():
        with sf() as session:
            yield session

    api.app.dependency_overrides[api.get_db] = get_db
    # /api/ask 与令牌校验走「按需开短会话」这条路，测试里同样指到临时 sqlite，
    # 否则单测会连到 .env 里配置的真实 MySQL。
    api.app.dependency_overrides[api.get_session_factory] = lambda: sf
    api.app.dependency_overrides[api.get_index] = lambda: FakeIndex()
    api.app.dependency_overrides[api.get_llm] = lambda: ("tok", "model")
    monkeypatch.setattr(api, "generate_answer", stub_answer)
    with TestClient(api.app) as c:
        c.app.state.sf = sf
        yield c
    api.app.dependency_overrides.clear()


def token(client: TestClient, username: str = "alice") -> str:
    r = client.post("/api/register", json={"username": username, "password": "passw0rd-123"})
    assert r.status_code == 201
    r = client.post("/api/login", json={"username": username, "password": "passw0rd-123"})
    assert r.status_code == 200
    return r.json()["token"]


def auth(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def test_未登录不能问答(client: TestClient):
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/api/ask", json={"question": "试用期最长多久"}).status_code == 401
    assert client.post("/api/ask", json={"question": "试用期最长多久"},
                       headers=auth("not-a-real-token")).status_code == 401


def test_密码错误与重复注册(client: TestClient):
    client.post("/api/register", json={"username": "bob", "password": "passw0rd-123"})
    r = client.post("/api/login", json={"username": "bob", "password": "wrong-password"})
    assert r.status_code == 401 and "或" in r.json()["detail"]      # 不区分用户名/密码错误
    assert client.post("/api/register", json={"username": "bob",
                                              "password": "passw0rd-123"}).status_code == 409


def test_弱参数被拦下(client: TestClient):
    t = token(client)
    assert client.post("/api/ask", json={"question": "短"}, headers=auth(t)).status_code == 422
    assert client.post("/api/register", json={"username": "a!/", "password": "passw0rd-123"}
                       ).status_code == 422
    assert client.post("/api/register", json={"username": "carol", "password": "123"}
                       ).status_code == 422


def test_问答落库与引用告警(client: TestClient):
    t = token(client)
    r = client.post("/api/ask", json={"question": "数据出境要评估吗"}, headers=auth(t))
    assert r.status_code == 200
    body = r.json()
    assert body["log_id"] and body["citations"][0]["article"] == "第三十条"
    assert body["warnings"] == {"suspicious": ["《中华人民共和国网络安全法》"], "mismatched": []}
    assert body["stats"]["tok_per_s"] == 8.0
    assert "截止 2025-01-01" in body["caveat"]          # 语料版本风险随答案一起给
    assert "retrieved" not in body                      # 默认不外泄入模片段

    dbg = client.post("/api/ask", json={"question": "数据出境要评估吗", "debug": True},
                      headers=auth(t)).json()
    assert dbg["retrieved"][0]["chunk_id"] == "law_056#30"
    assert dbg["retrieved"][0]["text"].startswith("第三十条 从事安全评估")

    db = client.app.state.sf()
    logs = db.query(QaLog).all()
    assert len(logs) == 2 and logs[0].user_id == db.query(api.User).first().id
    assert logs[0].warnings["suspicious"] == ["《中华人民共和国网络安全法》"]
    assert logs[0].top1_sim == 0.71


def test_历史回看取回完整记录(client: TestClient):
    t = token(client)
    log_id = client.post("/api/ask", json={"question": "数据出境要评估吗", "debug": True},
                         headers=auth(t)).json()["log_id"]
    client.post("/api/feedback", json={"log_id": log_id, "rating": -1}, headers=auth(t))
    full = client.get(f"/api/logs/{log_id}", headers=auth(t)).json()
    assert full["question"] == "数据出境要评估吗"
    assert full["retrieved"][0]["chunk_id"] == "law_056#30"
    assert full["warnings"]["suspicious"] == ["《中华人民共和国网络安全法》"]
    assert full["feedback"] == -1
    other = token(client, "erin")
    assert client.get(f"/api/logs/{log_id}", headers=auth(other)).status_code == 404
    assert client.get("/api/logs/999999", headers=auth(t)).status_code == 404


def test_未配_DSN_时接口给_503_而不是让进程退出(client: TestClient, monkeypatch):
    """.env 还没建时（DSN 为空），/api/* 要能明确报错，uvicorn 不能跟着挂。"""
    def boom(url=None):
        raise SystemExit("缺少 KBRA_MYSQL_DSN，复制 .env.example 为 .env 并填入数据库连接串")

    monkeypatch.setattr(api, "session_factory", boom)
    # 两个取会话的依赖都回到真实实现：请求级（get_db）与短会话工厂都要能兜住
    client.app.dependency_overrides.pop(api.get_db)
    client.app.dependency_overrides.pop(api.get_session_factory)
    r = client.get("/api/history", headers=auth("whatever"))
    assert r.status_code == 503 and "KBRA_MYSQL_DSN" in r.json()["detail"]
    assert client.get("/health").status_code == 200      # 存活探针不依赖数据库


def test_ask_不在生成期间持有请求级会话(client: TestClient):
    """压测暴露的缺陷：/api/ask 若依赖请求级会话，20 并发会把连接池占满返回 500。

    登录后把 get_db 换成「一进来看就炸」的实现：/api/ask 仍能 200，
    说明它只走 `get_session_factory` 那条短会话路径，慢的生成阶段不碰连接池。
    """
    t = token(client, "carol")

    def explode():
        raise AssertionError("ask 不该再用请求级会话")
        yield                                                # pragma: no cover

    client.app.dependency_overrides[api.get_db] = explode
    r = client.post("/api/ask", json={"question": "个人数据出境要评估吗"}, headers=auth(t))
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    with client.app.state.sf() as db:
        questions = db.scalars(select(api.QaLog.question)).all()
    assert questions == ["个人数据出境要评估吗"]              # 日志照常落库


def test_生成跑在专用线程而不是请求线程(client: TestClient, monkeypatch):
    """压测定位的第二个瓶颈：同步路由 + 锁会让 100 并发的生成占满 anyio 线程池，
    毫秒级的读库排队等线程。改成 async 路由 + `GEN_POOL` 之后生成必须在 `gen` 线程里跑。
    """
    import threading

    seen = []

    def record_thread(question, idx, llm=None, k=4, **_):
        seen.append(threading.current_thread().name)
        return stub_answer(question, idx, llm=llm, k=k)

    monkeypatch.setattr(api, "generate_answer", record_thread)
    t = token(client, "frank")
    assert client.post("/api/ask", json={"question": "数据出境要评估吗"},
                       headers=auth(t)).status_code == 200
    assert seen and all(n.startswith("gen") for n in seen), seen


def test_前端页面同源可取(client: TestClient):
    """web/ 由 StaticFiles 挂在 /，接口与页面同源，省掉 CORS。"""
    page = client.get("/")
    assert page.status_code == 200 and "企业知识库问答系统" in page.text
    assert client.get("/app.js").status_code == 200
    # mount 不抢 API 自己的方法：POST /api/ask 仍然进路由（无令牌 → 401 而不是静态页 404）
    r = client.post("/api/ask", json={"question": "试用期最长多久"})
    assert r.status_code == 401
    assert client.get("/没有这个页面.html").status_code == 404


def test_反馈与历史(client: TestClient):
    t = token(client)
    log_id = client.post("/api/ask", json={"question": "试用期最长多久"},
                         headers=auth(t)).json()["log_id"]
    other = token(client, "dave")
    # 别人的记录改不了
    assert client.post("/api/feedback", json={"log_id": log_id, "rating": 1},
                       headers=auth(other)).status_code == 404
    assert client.post("/api/feedback", json={"log_id": log_id, "rating": 0},
                       headers=auth(t)).status_code == 422
    r = client.post("/api/feedback", json={"log_id": log_id, "rating": -1, "comment": "条号不对"},
                    headers=auth(t))
    assert r.json() == {"log_id": log_id, "rating": -1}
    # 同一条记录再点是赞 → 覆盖而不是插新行
    client.post("/api/feedback", json={"log_id": log_id, "rating": 1}, headers=auth(t))
    db = client.app.state.sf()
    assert db.query(Feedback).count() == 1 and db.query(Feedback).first().rating == 1

    items = client.get("/api/history", headers=auth(t)).json()["items"]
    assert items[0]["log_id"] == log_id and items[0]["feedback"] == 1
    assert client.get("/api/history?limit=0", headers=auth(t)).json()["items"]  # 夹到下限


def test_登出后令牌失效(client: TestClient):
    t = token(client)
    assert client.post("/api/logout", headers=auth(t)).status_code == 200
    assert client.get("/api/history", headers=auth(t)).status_code == 401
