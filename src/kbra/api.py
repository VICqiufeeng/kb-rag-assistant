"""M5 服务入口：FastAPI 接口 + Bearer 鉴权 + 问答日志落库。

启动（项目根目录，显存需约 6GB）：
    E:/DevEnv/venvs/kb-rag-assistant/Scripts/python.exe -m uvicorn kbra.api:app --app-dir src --port 8000

索引和 3B 模型都在首次请求时才加载（`app.state` 缓存），所以 /health 秒回、
第一条 /api/ask 慢一次属正常。单进程即可：模型常驻显存，多 worker 会各占 6GB。
生成走单线程池 `GEN_POOL` 串行（理由写在该常量旁）。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from .config import WEB_DIR
from .db import (
    AuthToken,
    Feedback,
    QaLog,
    User,
    hash_password,
    hash_token,
    issue_token,
    session_factory,
    user_by_token,
    verify_password,
)
from .generate import answer as generate_answer  # 单测里 monkeypatch 这个符号
from .generate import version_caveat
from .index import Index

# 用户名/密码规则：bcrypt 只吃 72 字节，超长直接 422 而不是静默截断
USERNAME_RE = r"^[A-Za-z0-9_.-]{3,32}$"


class RegisterIn(BaseModel):
    username: str = Field(pattern=USERNAME_RE)
    password: str = Field(min_length=8, max_length=64)


class LoginIn(BaseModel):
    username: str
    password: str = Field(min_length=1, max_length=64)


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    k: int = Field(4, ge=1, le=8)
    debug: bool = False


class FeedbackIn(BaseModel):
    log_id: int
    # Literal 而不是 Field(ne=0)：pydantic v2 会静默忽略 ne 这种自定义关键字
    rating: Literal[-1, 1]
    comment: str | None = Field(None, max_length=500)


app = FastAPI(title="企业知识库问答系统", version="0.5")
app.state.index = None
app.state.llm = None
_bearer = HTTPBearer(auto_error=False)

# 一张 8GB 卡上并行跑多个 model.generate 只会互相拖慢：压测实测 1 并发单次 5.5s
# （0.18 req/s），10 并发单次 49–53s（聚合仍只有 0.2 req/s），显存还顶到 7.8/8.2GB。
# 串行之后聚合吞吐不变、单次延迟可预期，也不会让 20 并发把显存打爆。
# 为什么是独立线程池而不是「同步路由 + 锁」：同步路由整条请求都占着一个 anyio
# worker 线程（默认 40 个），100 并发时线程全卡在生成锁上，毫秒级的 /api/history
# 实测被拖到 avg 31s / P50 50s。改成 async 路由 + 单 worker 池后排队发生在池里，
# 不再消耗 API 线程池。
GEN_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gen")


def get_db():
    """DSN 没配时给接口一个明确的 503，而不是让 uvicorn worker 直接退出。"""
    try:
        sf = session_factory()
    except SystemExit as e:
        raise HTTPException(503, str(e)) from e
    with sf() as db:
        yield db


def get_session_factory():
    """给路由一个「按需开短会话」的入口，不在慢操作期间占用连接。

    压测实测：/api/ask 一次要跑几秒到几十秒，若沿用请求级会话（`get_db`），
    20 并发就会把 SQLAlchemy 默认连接池（5 + 10）占满，后面的请求等 30s 后抛
    `QueuePool limit … connection timed out` → HTTP 500。慢的那段是 GPU 生成，
    和数据库无关，所以令牌读取与日志写入各自开一个几毫秒的会话。
    """
    try:
        return session_factory()
    except SystemExit as e:
        raise HTTPException(503, str(e)) from e


def get_index(request: Request) -> Index:
    if request.app.state.index is None:
        request.app.state.index = Index()
    return request.app.state.index


def get_llm(request: Request):
    if request.app.state.llm is None:
        from .generate import load_llm
        request.app.state.llm = load_llm()
    return request.app.state.llm


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 sf=Depends(get_session_factory)) -> User:
    if cred is None:
        raise HTTPException(401, "缺少 Bearer 令牌")
    with sf() as db:                     # 读完就还连接，不在生成期间持有
        user = user_by_token(db, cred.credentials)
    if user is None:
        raise HTTPException(401, "令牌无效或已过期")
    return user


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "index_loaded": app.state.index is not None}


@app.post("/api/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "用户名已存在")
    db.add(User(username=body.username, pw_hash=hash_password(body.password)))
    db.commit()
    return {"username": body.username}


@app.post("/api/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.pw_hash):
        # 不区分「用户不存在」和「密码错」，避免枚举用户名
        raise HTTPException(401, "用户名或密码错误")
    raw, expires = issue_token(db, user)
    return {"token": raw, "expires_at": expires.isoformat(timespec="seconds")}


@app.post("/api/logout")
def logout(user: User = Depends(current_user), cred=Depends(_bearer),
           db: Session = Depends(get_db)):
    db.execute(delete(AuthToken).where(AuthToken.token_hash == hash_token(cred.credentials)))
    db.commit()
    return {"status": "ok"}


@app.post("/api/ask")
async def ask(body: AskIn, user: User = Depends(current_user),
              idx: Index = Depends(get_index), llm: tuple = Depends(get_llm),
              sf=Depends(get_session_factory)):
    # 生成在 GEN_POOL 里排队；await 期间这条请求不占 anyio worker 线程，也不持连接
    r = await asyncio.get_running_loop().run_in_executor(
        GEN_POOL, partial(generate_answer, body.question, idx, llm=llm, k=body.k))
    warnings = {"suspicious": r["suspicious_citations"], "mismatched": r["citation_mismatches"]}
    log = QaLog(
        user_id=user.id,
        question=body.question,
        answer=r["answer"],
        refused=r["refused"],
        top1_sim=r["stats"]["top1_sim"],
        citations=r["citations"],
        # 带上原文截断，前端「引用展开」和检索调试面板直接用，不再回查索引
        retrieved=[{"chunk_id": c["chunk_id"], "title": c["title"],
                    "article": c["article"], "score": c.get("score"),
                    "text": c["text"][:500]}
                   for c in r["retrieved"]],
        stats=r["stats"],
        warnings=warnings,
        caveat=version_caveat(r["retrieved"]),
    )
    with sf() as db:
        db.add(log)
        db.commit()
    out = {"log_id": log.id, "answer": log.answer, "refused": log.refused,
           "citations": log.citations, "warnings": warnings,
           "caveat": log.caveat, "stats": log.stats}
    if body.debug:
        out["retrieved"] = log.retrieved
    return out


@app.get("/api/logs/{log_id}")
def get_log(log_id: int, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    """前端历史回看用：把落库的完整记录（含入模原文）取回来。"""
    log = db.get(QaLog, log_id)
    if log is None or log.user_id != user.id:
        raise HTTPException(404, "问答记录不存在")
    fb = db.scalar(select(Feedback).where(Feedback.log_id == log_id))
    return {"log_id": log.id, "question": log.question, "answer": log.answer,
            "refused": log.refused, "citations": log.citations,
            "warnings": log.warnings, "caveat": log.caveat, "stats": log.stats,
            "retrieved": log.retrieved, "feedback": fb.rating if fb else None,
            "created_at": log.created_at.isoformat(timespec="seconds")}


@app.post("/api/feedback")
def feedback(body: FeedbackIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    log = db.get(QaLog, body.log_id)
    if log is None or log.user_id != user.id:
        raise HTTPException(404, "问答记录不存在")
    row = db.scalar(select(Feedback).where(Feedback.log_id == body.log_id))
    if row is None:
        db.add(Feedback(log_id=body.log_id, user_id=user.id,
                        rating=body.rating, comment=body.comment))
    else:
        row.rating, row.comment = body.rating, body.comment
    db.commit()
    return {"log_id": body.log_id, "rating": body.rating}


@app.get("/api/history")
def history(limit: int = 20, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    logs = db.scalars(select(QaLog).where(QaLog.user_id == user.id)
                      .order_by(desc(QaLog.id)).limit(limit)).all()
    ratings = {f.log_id: f.rating for f in
               db.scalars(select(Feedback).where(Feedback.user_id == user.id))}
    return {"items": [{
        "log_id": row.id, "question": row.question, "answer": row.answer,
        "refused": row.refused, "citations": row.citations, "warnings": row.warnings,
        "caveat": row.caveat, "feedback": ratings.get(row.id), "created_at":
            row.created_at.isoformat(timespec="seconds"),
    } for row in logs]}


# 前端与接口同源（一个进程、无 CORS），mount 放最后以免吃掉 /api 与 /health
if WEB_DIR.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
