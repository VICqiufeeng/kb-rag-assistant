# -*- coding: utf-8 -*-
"""下载模型权重到 E 盘缓存，供本地加载使用。

为什么不用 huggingface_hub：hub 1.21 会校验响应域名，走镜像抛
FileMetadataError；且 hf-mirror 对 API 请求返回 308 跳回 huggingface.co（被墙）。

用法：
    python scripts/fetch_model.py BAAI/bge-small-zh-v1.5            # hf 镜像
    python scripts/fetch_model.py --ms Qwen/Qwen2.5-3B-Instruct     # ModelScope
"""
import argparse
import json
from pathlib import Path

import requests

HF = "https://hf-mirror.com"
MS = "https://www.modelscope.cn"
CACHE = Path("E:/DevEnv/hf-cache/models")
# 其他框架的权重副本，下了也用不到
SKIP_SUFFIX = (".h5", ".msgpack", ".onnx", ".pt", ".bin", ".gguf")
SKIP_PART = ("tf_model_", "flax")


def skipped(name: str) -> bool:
    return name.endswith(SKIP_SUFFIX) or any(p in name for p in SKIP_PART)


def get(url, **kw):
    """跟随重定向，但把跳回 huggingface.co 的重定向改写回镜像。"""
    for _ in range(5):
        r = requests.get(url, allow_redirects=False, timeout=60, **kw)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers["location"]
            url = loc.replace("huggingface.co", "hf-mirror.com")
            continue
        return r
    raise RuntimeError(f"重定向过多: {url}")


def list_files(repo_id: str, source: str):
    if source == "ms":
        r = get(f"{MS}/api/v1/models/{repo_id}/repo/files?Recursive=true")
        r.raise_for_status()
        data = r.json()["Data"]
        entries = data.get("Files") or []
        return [(f["Path"], f.get("Size", 0)) for f in entries if f.get("Type", "blob") == "blob"]
    r = get(f"{HF}/api/models/{repo_id}")
    r.raise_for_status()
    return [(s["rfilename"], 0) for s in r.json()["siblings"]]


def download(url: str, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(out)
    return out.stat().st_size


def fetch(repo_id: str, source: str) -> Path:
    dest = CACHE / repo_id.split("/")[-1]
    base = f"{MS}/models/{repo_id}/resolve/master" if source == "ms" else f"{HF}/{repo_id}/resolve/main"
    got = 0
    for name, size in list_files(repo_id, source):
        if skipped(name):
            continue
        out = dest / name
        if out.exists() and out.stat().st_size == size:
            print(f"  have  {name}  {out.stat().st_size/1e6:.1f} MB", flush=True)
            continue
        n = download(f"{base}/{name}", out)
        got += n
        print(f"  got   {name}  {n/1e6:.1f} MB", flush=True)
    print(f"{repo_id} -> {dest}  本次下载 {got/1e6:.0f} MB", flush=True)
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ms", action="store_true", help="从 ModelScope 拉取")
    ap.add_argument("repos", nargs="+")
    args = ap.parse_args()
    for repo in args.repos:
        fetch(repo, "ms" if args.ms else "hf")
