"""路径与配置常量（环境相关路径统一走环境变量，默认指向 E:/DevEnv）。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")     # 路径与密码都在 .env（已 gitignore），代码库里不写明文
RAW_DIR = ROOT / "data" / "raw"
MANIFEST = RAW_DIR / "manifest.json"
CHUNKS = ROOT / "data" / "chunks.jsonl"
INDEX_DIR = ROOT / "data" / "index"
VECTORS = INDEX_DIR / "vectors.npy"
META = INDEX_DIR / "meta.json"
EVAL_SET = ROOT / "eval" / "qa_set.jsonl"
WEB_DIR = ROOT / "web"

EMBED_MODEL = Path(os.environ.get("KBRA_EMBED_MODEL", r"E:/DevEnv/hf-cache/models/bge-small-zh-v1.5"))
LLM_MODEL = Path(os.environ.get("KBRA_LLM_MODEL", r"E:/DevEnv/hf-cache/models/Qwen2.5-3B-Instruct"))
RERANK_MODEL = Path(os.environ.get("KBRA_RERANK_MODEL", r"E:/DevEnv/hf-cache/models/bge-reranker-base"))

# 服务化（M5）：DSN 从 .env 的 KBRA_MYSQL_DSN 读，密码不写进代码库
DB_URL = os.environ.get("KBRA_MYSQL_DSN", "")
TOKEN_TTL_HOURS = int(os.environ.get("KBRA_TOKEN_TTL_HOURS", "72"))
