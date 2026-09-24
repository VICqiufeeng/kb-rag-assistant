"""M5 建库建表 + 创建首个账号（幂等，可重复跑）。

用法（项目根目录，先在 .env 填好 KBRA_MYSQL_DSN）：
    python scripts/init_db.py --username admin              # 密码交互输入
    python scripts/init_db.py --username admin --password 'xxx'
"""
import argparse
import getpass
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, inspect, select, text   # noqa: E402
from sqlalchemy.engine import make_url                    # noqa: E402

from kbra.config import DB_URL                            # noqa: E402
from kbra.db import User, engine_for, hash_password, session_factory  # noqa: E402


def ensure_database(dsn: str) -> str:
    """建 schema（MySQL）；sqlite DSN 没有 schema 概念，直接跳过。"""
    url = make_url(dsn)
    if url.drivername.startswith("sqlite"):
        return f"sqlite 文件库 {url.database}，无需建 schema"
    dbname = url.database or ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", dbname):
        raise SystemExit(f"库名不合法：{dbname!r}")
    engine = create_engine(url.set(database=""), pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{dbname}` CHARACTER SET utf8mb4"))
        conn.commit()
    engine.dispose()
    return f"MySQL schema `{dbname}` 就绪"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password")
    args = ap.parse_args()
    if not DB_URL:
        raise SystemExit("缺少 KBRA_MYSQL_DSN：复制 .env.example 为 .env 并填入连接串")

    print(ensure_database(DB_URL))
    engine = engine_for()           # 内部 create_all，重复跑只补缺表
    tables = " ".join(sorted(inspect(engine).get_table_names()))
    sf = session_factory()
    with sf() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if user:
            print(f"用户 {args.username} 已存在（id={user.id}），未改密码")
        else:
            pw = args.password or getpass.getpass(f"为 {args.username} 设置密码（≥8 位）：")
            if len(pw.encode()) < 8 or len(pw.encode()) > 72:
                raise SystemExit("密码长度需在 8–72 字节之间")
            db.add(User(username=args.username, pw_hash=hash_password(pw)))
            db.commit()
            print(f"已创建用户 {args.username}")
    print("表：" + (tables or "（读表清单失败，见 SQLAlchemy 元数据）"))


if __name__ == "__main__":
    main()
