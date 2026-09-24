# 企业知识库问答系统（RAG）

把企业文档（制度 / 手册 / 工单 FAQ）变成可问答的服务：提问 → 混合检索 → 重排 → 本地 LLM 生成 → 带引用溯源的回答，并用自建评测集量化每一层的效果。

> **状态**：检索、生成、服务化、前端、评测、压测六段均已跑通；28 项单测；
> CI（ruff + pytest 矩阵 3.10 / 3.12 / 3.14）在 GitHub 上 4 个 job 全绿。
> **语料与向量索引不入库**，需按「快速开始」自行构建；未做容器化。

---

## 检索与生成链路

```
语料（400 篇）
   │  按结构边界切块 → 15993 块（平均 134 字）
   ▼
┌──────────────── 双路索引 ────────────────┐
│  稠密向量  bge-small-zh-v1.5（512 维）    │
│  稀疏检索  BM25（jieba 分词）             │
└───────────────────┬─────────────────────┘
                    ▼  RRF 融合（candidate=20）
              BGE 交叉编码器重排
                    ▼
        Top-k 拼 Prompt → 本地 Qwen2.5-3B-Instruct 生成
                    ▼
    引用溯源 [n] → 法名↔条号一致性校验 → 拒答判定
```

切块按结构边界而非固定长度：法条「一行即一条」并以行首条号判定。按「第X条」正则硬切会把《民法典》撕成 2582 个碎片（平均 67 字），改用行判定后回到 1260 块 = 实际条数。

## 关键数字

### 检索层（103 题，Recall@5，同一份 15993 块索引）

| 配置 | Recall@5 | MRR | 未命中 |
|---|---|---|---|
| 仅稠密向量（bge-small） | 0.961 | 0.888 | 4 |
| 仅 BM25（jieba 分词） | 0.981 | 0.900 | 2 |
| 混合 RRF（candidate=20 / 40 结果相同） | 0.981 | 0.910 | 2 |
| 混合 + BGE 重排 | 0.981 | **0.930** | 2 |

- 法条语料上 BM25 略强于纯向量：条号与法律名是强字面信号，稠密向量会漂。
- RRF 融合消掉两路各自独有的错（稠密 4 漏 → 混合 2 漏）。
- 重排不提 Recall、只提 MRR（0.910 → 0.930）：它只能重排候选池内的块。剩余 2 题（著作权法第五条、食品安全法第六十三条）未进 20 条候选池，重排后 Top-5 仍不含 gold。提 Recall 需动召回，不是动排序。

### 生成层（40 道正例等距抽样 + 全部 14 道负例，k=4，本地 Qwen2.5-3B-Instruct fp16）

| 指标 | 值 | 口径 |
|---|---|---|
| 回答带 `[n]` 引用 | 0.925 | 37/40 正例 |
| 引用命中 gold 条文 | 1.000 | 带引用的回答中 |
| 凭空引用法律名 | 0.000 | 答案提到、检索材料中没有 |
| 法名↔编号 / 条号错配 | 0.000 | 校验器判定 |
| 负例正确拒答 | 0.929 | 13/14 |
| 检索层 k=4 命中 gold | 1.000 | 抽样 40 题 |

唯一未拒答的负例：模型用带薪年休假的一般规则硬答了企业内部政策问题，属该拒未拒。

### 服务与压测（单进程 uvicorn，单卡 RTX 3070 Laptop 8GB，u=100 / 5min）

| 指标 | 修复前（生成占请求线程） | 修复后（`GEN_POOL`） |
|---|---|---|
| `读库` avg | 31.27s | **0.404s** |
| `读库` P50 / P95 | 50.00s / 61.00s | 0.310s / 0.875s |
| `读库` 最快 | — | 10.6ms |
| `生成` avg / P50 | 198.2s / 202s | 149.1s / 150s |
| `生成` P95 | 289s | 283s |
| `生成` 完成数 / 聚合吞吐 | 38 / 0.129 req/s | 60 / 0.204 req/s |
| `登录` avg（100 次） | 1.11s | 0.99s |
| 失败 / 异常 | 0 | 0，无 CUDA OOM，峰值显存 7409/8192 MiB |

生成速度与端到端：热态单次 12.1–15.1 tok/s，单次问答 18–152 token ≈ 1.3–16s，8GB 显存够用。

## 压测暴露的三个并发缺陷

三个都只在并发下出现，单测与串行运行均无法暴露。

1. **请求级会话占满连接池 → 20 并发全量 500。** `/api/ask` 用请求级 `Session`，一次生成跑几秒到几十秒，数据库连接在整个生成期间被占用；15 个槽位被 20 个并发占满后排队超时（`QueuePool limit of size 5 overflow 10 reached`）。改为令牌读取与日志写入各开毫秒级短会话，`/api/ask` 不再依赖请求级会话。
2. **单卡并行生成互相拖慢。** 8GB 显存上并行跑 `model.generate` 聚合吞吐不涨、显存顶到 7.8/8.2GB。用 `GEN_POOL`（`ThreadPoolExecutor(max_workers=1)`）串行化生成。
3. **慢接口占住 API 线程池。** 生成跑在请求线程上时，只碰 MySQL 的 `读库` 被拖到 avg 31.3s；把生成移入独立线程池后掉回 0.40s。收益不在「生成变快」（P95 289s → 283s 基本未动），而在两条路径解耦。

吞吐上限来自单卡串行生成，加并发只增加排队深度。按 0.204 req/s/实例折算，支撑 1 QPS 约需 5 个带卡实例。

## 引用一致性校验

生成层对每条引用做「法名 ↔ 条号」配对核验，抓到过 3B 模型的真实失误：把「第三十七条」挂到《数据安全法》名下，而资料里该法只有第三十 / 三十一 / 三十五条，第三十七条属《网络安全法》。

校验只认**紧贴法名**的条号串，因此「依照本法第四十条」这类法条交叉引用不会被误报。条号做了归一化比较（模型写「第35条」、语料写「第三十五条」不会误判）。

## 拒答的两条路径

- 模型自判：输出 `【无法回答】`，用于语料外问题（实测「2026 年世界杯冠军」等负例均走此路）。
- 低相似度短路：首条稠密相似度 < `MIN_SIM=0.30` 时在调用模型前直接拒答，省一次推理。实测**一次都没触发** —— 中文负例 `top1_sim` 最低 0.406，英文问题 0.419、SQL 语法问题 0.420，bge 在该语料上相似度整体偏高。阈值若上调到 0.45 以上，负例会在模型判断前被拦掉，拒答率需重测。

## 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| 文档解析 | `python-docx`（flk 官方 docx 正文）+ 正则清洗 | 无外部服务依赖，纯本地。PDF / Markdown 解析未做 |
| 嵌入模型 | `BAAI/bge-small-zh-v1.5`（sentence-transformers） | 中文小模型，可离线，零 API 成本 |
| 向量库 | numpy 全量余弦（1.6 万块量级） | 少一个依赖；语料到 10 万块再换 faiss / Chroma |
| 稀疏检索 | BM25（rank_bm25）+ RRF 融合 | 混合检索是该类系统的常见要求，也是实测中的提分点 |
| 重排 | `BAAI/bge-reranker-base` | 对比加 / 不加重排的 Recall / MRR 差异，见检索层表 |
| LLM | 本地 Qwen2.5-3B-Instruct（transformers） | 评测数字可复现，不受云端模型变更影响；保留 OpenAI 兼容接口作为可选后端 |
| 后端 | FastAPI + Uvicorn + pydantic v2 | 一次性出词，未做流式 |
| 数据库 | MySQL 8.0.46（用户 / 令牌 / 问答日志 / 反馈） | 四表均 InnoDB / utf8mb4_0900_ai_ci，`citations` / `retrieved` / `stats` / `warnings` 为原生 JSON 列 |
| 前端 | 原生 HTML + CSS + fetch，由 `StaticFiles` 挂在 `/` | 不引入构建链；生成端非流式，界面用「提问中…+ 端到端计时」 |
| 压测 | locust 2.46.6（无界面模式 + CSV） | 抓出上述三个并发缺陷 |
| 打包 | `pyproject.toml`（setuptools，`src/` 布局）+ 6 个 extras | 核心依赖只含 `import kbra.api` 需要的 6 个包，torch 相关推到 extras，无 GPU 的 CI 机 `pip install -e ".[dev]"` 即可跑单测 |
| CI | GitHub Actions：ruff + pytest 矩阵 3.10 / 3.12 / 3.14 | 语料、GPU、MySQL 均不参与 |

## 快速开始

语料、索引产物均不入库（`data/raw/*`、`data/chunks.jsonl`、`data/index/` 已在 `.gitignore`），clone 后需先构建才能跑评测或起服务。

```bash
pip install -r requirements.txt
# 或用 pyproject：pip install -e ".[full]"      （清单锁版本，extras 给范围）
# 只跑单测不需要语料和 GPU：pip install -e ".[dev]"

# 1) 拉三个模型（脚本走镜像直链，绕开 huggingface_hub 对镜像的 FileMetadataError）
python scripts/fetch_model.py BAAI/bge-small-zh-v1.5
python scripts/fetch_model.py BAAI/bge-reranker-base
python scripts/fetch_model.py --ms Qwen/Qwen2.5-3B-Instruct

# 2) 抓语料（四个来源，产物写 data/raw/ 并维护 manifest.json）
python scripts/fetch_corpus.py --queries 数据安全 个人信息保护 人工智能 数字经济 --per-query 5
python scripts/import_ms_dataset.py       # 补全文集中缺失的法律
python scripts/import_chinese_laws.py     # 177 部现行法律
python scripts/fetch_flk.py 个人信息保护法 劳动合同法

# 3) 切块 + 建索引（打印 chunks= / docs=，参考值 15993 块 / 400 篇）
PYTHONPATH=src python scripts/build_index.py

# 4) 评测集自检（gold 的「法名+条号」能否解析到真实块）
python scripts/validate_qa.py

# 5) 检索层 A/B（只吃索引，不需要生成）
python scripts/run_eval.py --out eval/retrieval.json
python scripts/run_eval.py --gen 40 --out eval/eval_full.json   # 加跑生成层，需 ~6GB 显存

# 6) 起服务
python scripts/init_db.py --username <用户名>      # 幂等建库建表建账号，密码走 getpass
python -m uvicorn kbra.api:app --app-dir src --port 8000
```

抓取脚本需访问 `gov.cn` / `flk.npc.gov.cn` / ModelScope，请自行确认网络可达并遵守各站点公开使用条款。政策篇目与法律现行版本会变，重建后块数与个别 Recall 值不会逐位相同。

模型默认落在 `$HF_HOME` 指向的缓存目录，可用 `KBRA_EMBED_MODEL` / `KBRA_RERANK_MODEL` / `KBRA_LLM_MODEL` 指到别处。走镜像时下载前需设 `HF_ENDPOINT=https://hf-mirror.com`。

### 环境变量

配置模板见 `.env.example`（全部占位）。数据库 DSN 从 `.env` 的 `KBRA_MYSQL_DSN` 读取，代码库中无明文密码。未配置时 `get_db` 返回 503 与提示文案，`/health` 仍返回 200。

```bash
KBRA_MYSQL_DSN=mysql+pymysql://root:在此填写密码@127.0.0.1:3308/kbra?charset=utf8mb4
```

## 接口

| 方法路径 | 鉴权 | 说明 |
|---|---|---|
| `GET /health` | 无 | 存活与索引加载状态，不触发模型加载 |
| `POST /api/register` | 无 | 用户名正则校验 + 密码 8–64 位（bcrypt 上限 72 字节） |
| `POST /api/login` | 无 | 返回不透明 Bearer 令牌，库中只存 SHA-256；默认 72h 过期 |
| `POST /api/logout` | Bearer | 删令牌行，旧令牌立即失效 |
| `POST /api/ask` | Bearer | `{question,k,debug}` → 答案 + 引用 + `warnings`（`suspicious` / `mismatched`）+ `caveat` + 统计；每次问答落一行 `qa_log` |
| `GET /api/logs/{id}` | Bearer | 单条记录完整回读（含入模原文）；他人记录返回 404 |
| `POST /api/feedback` | Bearer | 点赞 / 点踩（`rating ∈ {1,-1}`），重复提交为覆盖 |
| `GET /api/history` | Bearer | 自己的问答记录，`limit` 夹在 1–100 |
| `GET /` 、`/app.js`… | 无 | `web/` 由 `StaticFiles` 挂在同一源，无需 CORS 配置 |

密码错误与用户不存在均返回 401 且文案相同，不泄露用户是否存在。

## 数据模型

`scripts/init_db.py` 建库建表建账号（幂等，对已存在用户不改密码）：

- `user`：`username` 唯一、`pw_hash`（bcrypt 自带盐）
- `auth_token`：`token_hash` 主键、`expires_at` 索引
- `qa_log`：问题 / 答案 / 是否拒答 / `top1_sim` / `citations` / `retrieved`（原文截断 500 字）/ `stats` / `warnings` / `caveat` + 时间索引，`user_id` 外键
- `feedback`：`log_id` 唯一（一条问答最多一个反馈）

## 评测集设计

`eval/qa_set.jsonl`：**117 题 = 103 正例 + 14 负例**。gold 只标「法律名 + 条号」（如 `中华人民共和国劳动合同法 / 第十九条`），跑评测时再解析成 `chunk_id`，换语料重编号无需重标。

`scripts/validate_qa.py` 校验每条标注可解析到真实块、且预设答案关键词确实出现在该块正文；出题时用 `scripts/eval_seed.py` 抽取含事实点的条文。

指标落盘：`eval/retrieval.json`（检索层）、`eval/eval_full.json`（含生成层）。

## 已知限制

- **措辞敏感导致的召回缺口已量化。** 同一知识点换问法名次差一截（以《个人信息保护法》第三十八条为例：「个人信息跨境提供需要满足什么条件」名次 1；「个人数据出境需要安全评估吗」名次 17，挤进 20 条候选池但不在 Top-5）。第二问命中的是《数据安全法》第三十一条，生成层会答得「像对但引错法」。根因在检索侧词面不匹配（数据 ↔ 信息），重排无法补救。候选解法：查询改写、按标题 + 条号做二次索引、加大候选池。
- **聚合吞吐受限于单卡串行生成**，稳定在 0.13–0.204 req/s，加并发只增加排队深度。
- **法条数据集自述数据截止 2025-01-01**，该风险提示随答案一起输出（`generate.version_caveat`），不假定其为现行版本。
- **压测分位数样本偏少**：`读库` 两轮样本仅 9 次与 19 次（100 个用户多数卡在生成排队中，5min 窗口挤不出更多读请求），只能说明量级差，不能当稳定分布引用。
- **未做容器化与流式输出**，当前以单进程方式运行。
- 文档解析仅支持 docx 与文本，未做 PDF / Markdown。

## 工程化

- **CI**：`.github/workflows/ci.yml`，ruff 静态检查 + pytest 矩阵（Python 3.10 / 3.12 / 3.14）。无 GPU、无 MySQL、无 `.env` 的干净环境下 `pip install -e ".[dev]"` 即可装能测 —— 依赖均为函数内延迟导入，接口单测用 `dependency_overrides` 把索引与模型换成桩、库指向临时 sqlite。
- **打包**：`src/` 布局，extras 为 `retrieval` / `ml` / `serve` / `bench` / `corpus` / `dev` / `full`。
- Windows 下跑 locust 需带 `PYTHONUTF8=1`：locust 2.46 会把仓库根 `pyproject.toml` 当默认配置文件按系统码（GBK）读取，撞上中文注释会报 `Couldn't parse TOML file: 'gbk' codec can't decode byte 0x80…`。

## 语料来源与许可

语料四类来源共 400 篇，出处与许可证记在 `data/raw/manifest.json` 与每个文件头部。仓库根的 `LICENSE`（MIT）只管本仓库的代码与文档。

| 来源 | 规模 | 说明 |
|---|---|---|
| gov.cn 政策文件库 | 20 篇 / 10.5 万字 | 国务院、国办发、国办函文号文件，含原文链接；gov.cn 标注「本文有删减」 |
| ModelScope `dengcao/Chinese-Laws` | 177 部 / 14597 条 / 193 万字 | Apache-2.0，一行一条；数据集自述数据截止 2025-01-01 |
| ModelScope `Panda233/china_legal_articles_samples` | 201 部的 1000 条 / 9.4 万字 | Apache-2.0，按法律名去重补全文集缺失部分（58 部重复已跳过） |
| 国家法律法规数据库 `flk.npc.gov.cn` | 2 部 / 172 条 / 2.3 万字 | 官方 docx 正文解析，带公布 / 施行日期与详情页链接，属现行文本 |

## 目录结构

```
kb-rag-assistant/
├── src/kbra/
│   ├── config.py       路径与 DSN（.env 加载）
│   ├── chunking.py     按结构边界切块
│   ├── embeddings.py   bge 稠密向量编码
│   ├── index.py        向量 + jieba/BM25 + RRF 融合检索
│   ├── rerank.py       BGE 交叉编码器重排
│   ├── generate.py     Prompt 组装、引用溯源、拒答、一致性校验
│   ├── evaluate.py     gold 解析与 Recall / MRR 打分
│   ├── db.py           用户 / 令牌 / 问答日志 / 反馈 + 密码与令牌哈希
│   └── api.py          FastAPI 路由
├── scripts/            入库、抓模型、检索演示、问答、评测、建库、压测入口
├── data/raw/           语料原文 + manifest.json（不入库）
├── data/chunks.jsonl   切块产物（不入库）
├── data/index/         向量与 BM25 索引（不入库）
├── eval/               117 题评测集与指标落盘
├── tests/              引用校验、段头清理、接口与鉴权单测
├── web/                前端（同源挂载，无框架无构建链）
├── .env.example        配置模板（全部占位）
└── .env                真实配置，已 gitignore（不入库）
```

## License

MIT —— 详见 [LICENSE](LICENSE)。
