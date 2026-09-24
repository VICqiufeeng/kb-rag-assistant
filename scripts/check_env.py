# -*- coding: utf-8 -*-
"""环境自检：GPU / 本地模型加载 / 嵌入编码耗时与检索命中。
模型需先用 scripts/fetch_model.py 下载到 E 盘缓存。
"""
import sys
import time
from pathlib import Path

MODEL_CACHE = Path("E:/DevEnv/hf-cache/models")
EMBED = MODEL_CACHE / "bge-small-zh-v1.5"

import torch

print("python   ", sys.version.split()[0])
print("torch    ", torch.__version__, "| cuda", torch.cuda.is_available())
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("gpu      ", p.name, "| %.1f GB" % (p.total_memory / 2**30))
print("cache    ", MODEL_CACHE)

if not EMBED.exists():
    sys.exit(f"缺少模型 {EMBED}，先跑：python scripts/fetch_model.py BAAI/bge-small-zh-v1.5")

from sentence_transformers import SentenceTransformer

device = "cuda" if torch.cuda.is_available() else "cpu"
t0 = time.perf_counter()
m = SentenceTransformer(str(EMBED), device=device)
print("load     %.1fs on %s" % (time.perf_counter() - t0, device))

docs = [
    "员工入职需提交身份证、学历证书与体检报告，三个工作日内完成建档。",
    "会议室预订通过 OA 系统提交，最长可提前三十天预约。",
    "年度绩效考核分为 S/A/B/C 四档，C 档需制定改进计划。",
]
q = "新员工入职要交哪些材料？"

t0 = time.perf_counter()
dvecs = m.encode(docs, normalize_embeddings=True)
qvec = m.encode([q], normalize_embeddings=True)
print("encode   %.2fs for %d texts" % (time.perf_counter() - t0, len(docs) + 1))
print("dim      ", dvecs.shape[1])

sims = (dvecs @ qvec[0]).round(3)
print("sims     ", list(sims), "| top1 doc%d" % sims.argsort()[::-1][0])

# bge 中文检索场景下查询需加指令前缀，实测对比
q_inst = "为这个句子生成表示以用于检索相关文章：" + q
s2 = (dvecs @ m.encode([q_inst], normalize_embeddings=True)[0]).round(3)
print("instruct ", list(s2), "| top1 doc%d" % s2.argsort()[::-1][0])
