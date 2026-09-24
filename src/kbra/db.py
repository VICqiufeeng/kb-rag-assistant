"""M5 数据层：用户、登录令牌、问答日志、点赞点踩反馈。

只存业务记录；语料与向量索引仍在 `data/` 下（重建索引不应该动业务库）。
时间统一存 UTC naive，避免跨区机器对账时口径不一致。
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        SmallInteger, String, Text, create_engine, select)
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            sessionmaker)

from .config import DB_URL, TOKEN_TTL_HOURS


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    pw_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthToken(Base):
    """不透明 Bearer 令牌：库里只存 SHA-256，明文只在登录响应里出现一次。"""

    __tablename__ = "auth_token"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QaLog(Base):
    __tablename__ = "qa_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    top1_sim: Mapped[float | None] = mapped_column(Float)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    # 入模资料（chunk_id + 名次 + 分数）与耗时/吞吐统计，检索调试面板要用
    retrieved: Mapped[list] = mapped_column(JSON, default=list)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    # 引用一致性告警，分两类存：{"suspicious": [凭空引用的法律], "mismatched": [名↔编号/条号错配]}
    warnings: Mapped[dict] = mapped_column(JSON, default=dict)
    # 语料版本提示（法条数据集自述截止 2025-01-01 时要随答案一起给）
    caveat: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("qa_log.id"), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


_ENGINES: dict[str, Engine] = {}
_FACTORIES: dict[str, sessionmaker] = {}


def engine_for(url: str | None = None) -> Engine:
    """建引擎并复用；首次连接时建表（幂等）。"""
    key = url or DB_URL
    if not key:
        raise SystemExit("缺少 KBRA_MYSQL_DSN，复制 .env.example 为 .env 并填入数据库连接串")
    if key not in _ENGINES:
        engine = create_engine(key, pool_pre_ping=True, pool_recycle=3600)
        Base.metadata.create_all(engine)
        _ENGINES[key] = engine
    return _ENGINES[key]


def session_factory(url: str | None = None) -> sessionmaker:
    key = url or DB_URL
    if key not in _FACTORIES:
        _FACTORIES[key] = sessionmaker(engine_for(url), expire_on_commit=False)
    return _FACTORIES[key]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), pw_hash.encode())
    except ValueError:      # 库里存了非法哈希（手工改库）时按认证失败处理
        return False


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(db: Session, user: User) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=TOKEN_TTL_HOURS)
    db.add(AuthToken(token_hash=hash_token(raw), user_id=user.id, expires_at=expires))
    db.commit()
    return raw, expires


def user_by_token(db: Session, raw: str) -> User | None:
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(raw)))
    if row is None or row.expires_at <= utcnow():
        return None
    return db.get(User, row.user_id)
