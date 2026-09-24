# -*- coding: utf-8 -*-
"""本地 LLM 冒烟测试：加载 Qwen2.5-3B-Instruct，量显存，跑一次带资料的生成。
模型需先用 scripts/fetch_model.py --ms 下载到 E 盘。
"""
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = Path("E:/DevEnv/hf-cache/models/Qwen2.5-3B-Instruct")
CORPUS = Path(__file__).resolve().parent.parent / "data" / "raw"

if not MODEL.exists():
    raise SystemExit(f"缺少模型目录 {MODEL}，先跑 fetch_model.py --ms Qwen/Qwen2.5-3B-Instruct")

doc = next(iter(sorted(CORPUS.glob("gov_*.txt"), key=lambda p: -p.stat().st_size)))
context = doc.read_text(encoding="utf-8")[:1200]
question = "这份文件对规划实施情况的评估和督导是怎么安排的？"

t0 = time.perf_counter()
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.float16, device_map="cuda")
print(f"load      {time.perf_counter() - t0:.1f}s | 权重显存 "
      f"{torch.cuda.memory_allocated() / 2**30:.2f} GiB")

msgs = [
    {"role": "system", "content": "你是政策文件问答助手。只依据给定资料回答，资料中没有的信息要直接说明未提及。"},
    {"role": "user", "content": f"【资料·{doc.name}】\n{context}\n\n【问题】{question}"},
]
prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
inputs = tok(prompt, return_tensors="pt").to(model.device)
print(f"prompt    {inputs['input_ids'].shape[1]} tokens")

t0 = time.perf_counter()
with torch.no_grad():
    out = model.generate(**inputs, max_new_tokens=220, do_sample=False)
gen = out[0][inputs["input_ids"].shape[1]:]
dt = time.perf_counter() - t0
print(f"generate  {dt:.1f}s for {len(gen)} tokens = {len(gen)/dt:.1f} tok/s")
print(f"peak vram {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
print("--- 回答 ---")
print(tok.decode(gen, skip_special_tokens=True).strip())
