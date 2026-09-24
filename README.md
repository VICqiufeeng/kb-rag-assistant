# 企业知识库问答系统（RAG）

完整工程闭环：**检索增强生成 + 后端服务 + 数据库 + 前端 + 评测指标**。
与毕设（深度强化学习路径规划）无关，独立仓库、独立技术栈。

## 目标

把一批企业文档（制度/手册/工单 FAQ）变成可问答的服务：提问 → 混合检索 → 带引用溯源的回答，
并用**自建评测集**量化每一步的效果。核心卖点不是"跑通 demo"，而是**有数字、有对比、有服务化**。

## 技术选型（已按本机实测条件定）

| 层 | 选型 | 理由 |
|---|---|---|
| 语言运行时 | Python **3.14** venv（`--system-site-packages` 复用全局 torch） | 全局已装 torch 2.12.1+cu130 且实测 CUDA 可用，新依赖只进 venv |
| 文档解析 | pypdf / python-docx / markdown-it | 无外部服务依赖，纯本地 |
| 嵌入模型 | `BAAI/bge-small-zh-v1.5`（sentence-transformers） | 中文小模型，8GB 显存轻松跑，可离线，零 API 成本 |
| 向量库 | numpy 全量余弦（1.6 万块量级足够，索引落盘 `data/index/`） | 少一个依赖；语料到 10 万块再换 faiss/Chroma |
| 稀疏检索 | BM25（rank_bm25）+ 与向量结果 RRF 融合 | 混合检索是这类系统的常见要求，也是实测里的真实提分点 |
| 重排 | `BAAI/bge-reranker-base`（本地 1.1GB，`src/kbra/rerank.py`） | 对比实验：加/不加 rerank 的 Recall/MRR 差异，见 M4 A/B 表 |
| LLM | **本地模型**：transformers 跑 Qwen2.5-Instruct（用户指定不走云 API） | 评测数字可复现，不受云端模型变更影响；保留 OpenAI 兼容接口作为可选后端 |
| 后端 | FastAPI + Uvicorn + pydantic v2 | 命中"后端开发"；一次性出词，**没做流式**（见「前端」行的理由） |
| 数据库 | **MySQL 8.0.46**（用户/令牌/问答日志/反馈），本项目专属实例 `E:\DevEnv\mysql-kbra` 端口 3308 | 命中 JD 的 MySQL、数据库标签；系统 `MySQL80`（3306）root 密码未知、3307 实例属另一条线，见「本机环境事实」 |
| 前端 | 原生 HTML + CSS + fetch，与接口同源（`StaticFiles` 挂在 `/`） | 不引入构建链；生成端是 `model.generate` 一次性出词，**没有做流式**，所以不用 SSE，界面用「提问中…+ 端到端计时」 |
| 压测 | locust 2.46.6（`scripts/locustfile.py`，无界面模式 + CSV） | 实测出并发下的真缺陷（连接池占满），数字见「M7 压测实测」 |
| 部署 | ~~Dockerfile + docker-compose~~ **未做**（2026-09-24 决定先压测、Docker 以后再说；本机也没装 Docker Desktop） | 当前以本机单进程方式运行，**不算已完成项** |

## 里程碑（每步都有可验收的产出）

- **M1 入库** ✅：语料下载 → 统一头部格式 → 按结构边界切块（法条一条一块 / 政策按段落打包）
- **M2 检索** ✅：向量 + BM25 混合（RRF 融合）+ BGE 交叉编码器重排，数字见 M4 的 A/B 表
- **M3 生成** ✅：Top-k 拼 Prompt → 本地 Qwen2.5-3B-Instruct 生成 → `[n]` 引用溯源 →
  低相似度与「资料不足」两类拒答 → **引用一致性校验**（抓出模型的张冠李戴）
- **M4 评测** ✅：`eval/qa_set.jsonl` 117 题（103 正例 / 14 负例），gold 按「法名+条号」标注并由
  `scripts/validate_qa.py` 校验；检索层 Recall@5 / MRR 四配置对比 + 生成层幻觉率 / 拒答率，
  全部实跑数字见下方「M4 结果」
- **M5 服务化** ✅：FastAPI 八个接口 + 四张表 + Bearer 登录鉴权 + 每次问答落库；
  真索引真模型走 HTTP 实测（数字见「M5 服务化实测」），单测全绿（当前 28 项）
  - 数据库已落在**本项目专属 MySQL 实例**（`E:\DevEnv\mysql-kbra`，端口 3308）上复跑通过，
    JSON 列、唯一约束、外键与级建表语句都在 MySQL 8.0.46 上实测（见「MySQL 真库复跑」）
- **M6 前端** ✅：问答界面 + 引用展开（原文/出处/许可证）+ 点赞点踩 + 检索调试面板 + 历史回看，
  与接口同源部署，浏览器真机走查通过（见「M6 前端实测」）
- **M7 压测** ✅（容器化暂缓）：locust 打真实链路，抓出并修掉两个只在并发下暴露的缺陷
  （请求级会话占满连接池 → 20 并发全 500；单卡并行生成互相拖慢）；
  修复后 100 并发 0 失败、P95 289s、聚合吞吐 0.13–0.19 req/s，数字见「M7 压测实测」

## 项目摘要（逐条对应下方实测记录，M4 段已回填真实数字）

> 独立完成企业知识库问答系统：自建 117 题评测集（103 正例 / 14 负例，gold 标注到法条级），
> 采用 BM25+向量混合检索（RRF）与 BGE 交叉编码器重排，Recall@5 由 0.961 提升至 0.981、
> MRR 由 0.888 提升至 0.930；本地 Qwen2.5-3B 生成 + 引用一致性校验，
> 抽样 40 题引用命中 gold 100%、零凭空引用，负例拒答准确率 92.9%；
> FastAPI 服务化（Bearer 鉴权 + MySQL 四表问答日志 + 反馈）与同源前端，28 项单测；
> locust 压测定位并修复「请求级会话占满连接池 → 20 并发全量 500」，修复后 100 并发
> 0 失败、P95 289s（单卡串行生成上限 0.19 QPS，已据此给出多实例换算）。（M7 压测已完成，容器化暂缓）

## 从零复现（语料与索引**不入库**，先看这段）

`data/raw/`（本机 400 个 `.txt`、4.2MB）、`data/chunks.jsonl`（15993 块）和 `data/index/`
（`vectors.npy` + `bm25.pkl`）都在 `.gitignore` 里——语料是从公开站点抓来的，不随仓库分发。
仓库根的 `LICENSE`（MIT）只管本仓库的代码与文档；语料各自的许可证记在
`data/raw/manifest.json` 与每个文件头部（ModelScope 两个数据集是 Apache-2.0，
政策文件是政府公开文本，flk.npc.gov.cn 无许可证字段、按「国家法律法规数据库公开文本」记录）。
**所以 clone 下来直接跑评测或起服务都会因为没有语料而失败**，下面这一串跑完才有本 README
里那些数字对应的那个东西。抓取脚本要访问 `gov.cn` / `flk.npc.gov.cn` / ModelScope，
请自行确认网络可达并遵守各站点的公开使用条款。

```bash
# 0) 依赖：先按官网命令装对应 CUDA 版本的 torch，再装清单（本项目 torch 不在清单里）
pip install -r requirements.txt
# 1) 三个模型（脚本走镜像直链，绕开 huggingface_hub 对镜像的 FileMetadataError）
python scripts/fetch_model.py BAAI/bge-small-zh-v1.5
python scripts/fetch_model.py BAAI/bge-reranker-base
python scripts/fetch_model.py --ms Qwen/Qwen2.5-3B-Instruct
#    默认落在作者的 E:\DevEnv\hf-cache\models\；换机器就用 .env 里这三个变量指过去：
#    KBRA_EMBED_MODEL / KBRA_RERANK_MODEL / KBRA_LLM_MODEL
# 2) 语料（四个来源，产物写 data/raw/ 并维护 manifest.json）
python scripts/fetch_corpus.py --queries 数据安全 个人信息保护 人工智能 数字经济 --per-query 5
python scripts/import_ms_dataset.py           # Panda233/china_legal_articles_samples → law_s*（201 篇）
python scripts/import_chinese_laws.py         # dengcao/Chinese-Laws，177 部现行法律 → law_*
python scripts/fetch_flk.py 个人信息保护法 劳动合同法   # 补上面数据集缺的两部 → flk_*
#    本机结果：20 gov + 201 law_s + 177 law + 2 flk = 400 篇（kind: policy 20 / law 380）
# 3) 切块 + 建索引（打印 chunks= / docs= ，本机是 15993 块 / 400 篇）
PYTHONPATH=src python scripts/build_index.py
# 4) 评测集自检：gold「法名+条号」能否解析到真实块；报错先修标注再跑评测
python scripts/validate_qa.py
# 5) 检索层 A/B（只吃索引，不需要生成）；--gen 40 会加载 3B，需 ~6GB 显存
python scripts/run_eval.py --out eval/retrieval.json
# 6) 服务：任意 MySQL 8 建个 schema，.env 填 KBRA_MYSQL_DSN，然后
python scripts/init_db.py --username <你的名字>     # 幂等建表 + 建账号
python -m uvicorn kbra.api:app --app-dir src --port 8000   # 打开 http://127.0.0.1:8000
```

第 2 步的抓取有**时效**：政策文件库的篇目、法律的现行版本都会变，所以你重建后的块数
和个别 Recall 值不会和本机逐位相同。本 README 的所有数字都标注了实测日期，
只作为这台机器这一天的一次记录，不是恒定值。

## 环境位置（一律放 E 盘，C 盘空间紧张）

| 用途 | 路径 |
|---|---|
| 项目代码 | `C:\Users\doush\Desktop\求职项目\kb-rag-assistant` |
| Python venv | `E:\DevEnv\venvs\kb-rag-assistant`（解释器 `Scripts\python.exe`） |
| pip 缓存 | `E:\DevEnv\pip-cache`（安装时加 `--cache-dir E:/DevEnv/pip-cache`） |
| 模型缓存 | `E:\DevEnv\hf-cache`（由 `HF_HOME` 指定） |
| 数据集/下载缓存 | `E:\DevEnv\dataset-cache`（ModelScope 数据集、flk.npc.gov.cn 的 docx 原文都落这里，不进 C 盘） |
| MySQL 数据目录 | `E:\DevEnv\mysql-kbra`（**项目专属实例，端口 3308**，启停脚本在 `E:\DevEnv\scripts\*-mysql-kbra.bat`） |

`E:\DevEnv` 是既有的**开发环境目录**（README 首行即标注"不是项目代码"），
本项目的 venv / 缓存 / 模型都并入该约定，不在 C 盘另建环境。

## 本机环境事实（2026-09-24 实测）

- GPU：RTX 3070 Laptop 8GB，驱动 616.56（服务停掉后显存回落到 1.3GB）
- 磁盘（本轮 `df` 复核）：E 盘剩 159GB（`mysql-kbra\data` 只占 195MB），C 盘剩 78GB
- 运行时：`E:\DevEnv\venvs\kb-rag-assistant`（Python 3.14.5，`--system-site-packages`）
  - 复用全局 `torch 2.12.1+cu130`，`torch.cuda.is_available() == True`
  - venv 内已装：transformers 5.17.0、sentence-transformers 6.1.0、
    scikit-learn、SQLAlchemy 2.0.51、httpx、rank_bm25、pytest、bcrypt 5.0.0、locust 2.46.6
  - 全局已有：fastapi 0.138.1、uvicorn 0.49.0、numpy 2.5.0、pandas 3.0.3
- **MySQL：另起本项目专属实例（端口 3308）**。系统服务 `MySQL80` 在 3306 Running 但
  **root 密码不在手上**，改它还要管理员权限；`E:\DevEnv\mysql` 那个 3307 实例是另一条线在用
  （实测有多个 ESTABLISHED 连接），不能混用。所以用**同一套 mysqld 程序只读引用**另起一个：
  ```
  配置文件   E:\DevEnv\mysql-kbra\my.ini
  数据目录   E:\DevEnv\mysql-kbra\data          （版本 8.0.46，utf8mb4_0900_ai_ci）
  端口       3308，bind-address = 127.0.0.1     （只监听本机，无对外暴露面）
  启停       E:\DevEnv\scripts\{start,stop,status}-mysql-kbra.bat
  连接串     项目根 .env 的 KBRA_MYSQL_DSN（.env 已 gitignore，代码库里无明文密码）
  ```
  初始化用 `--initialize-insecure`，随后**立刻** `ALTER USER 'root'@'localhost'` 设密码，
  再起 `python scripts/init_db.py` 建库建表建账号（幂等）。
- **模型下载必须走镜像**：`huggingface.co` 直连超时，`hf-mirror.com` 正常 →
  下载前需 `HF_ENDPOINT=https://hf-mirror.com`
- 未安装：Docker Desktop → 2026-09-24 已决定 M7 只做压测、容器化暂缓（装它要管理员权限
  且要开 WSL2），本轮全部实测都在本机进程方式下完成

## 已验证（2026-09-24 实跑）

```
E:\DevEnv\venvs\kb-rag-assistant\Scripts\python.exe scripts/check_env.py
```

- torch 2.12.1+cu130 调 CUDA 正常，识别 RTX 3070 8GB
- `bge-small-zh-v1.5` 已下载到 `E:\DevEnv\hf-cache\models\`（96.4MB）
- GPU 加载 0.5s；4 条中文文本编码 0.63s；向量维度 512
- 检索冒烟测试：问"新员工入职要交哪些材料"，top1 正确命中入职制度那段（相似度 0.647，
  干扰项 0.45 左右）；加 bge 查询指令前缀后 top1 不变
- **坑：`huggingface_hub` 1.21 与 hf-mirror 不兼容**，会抛
  `FileMetadataError: Distant resource does not seem to be on huggingface.co`。
  已绕开：用 `scripts/fetch_model.py <repo_id>` 走镜像 resolve 直链下载到 E 盘，
  代码里一律按**本地路径**加载模型，不要把 `snapshot_download` 写回业务代码

## 语料与链路实测（M1 / M2 / M3，2026-09-24）

语料四类来源（共 400 篇），都带可点击出处与许可证，写在 `data/raw/manifest.json` 里：

| 来源 | 规模 | 说明 |
|---|---|---|
| gov.cn 政策文件库 | 20 篇 / 10.5 万字 | 国务院、国办发、国办函文号文件，含原文链接；gov.cn 标注「本文有删减」 |
| ModelScope `dengcao/Chinese-Laws` | 177 部法律全文 / 14597 条 / 193 万字 | Apache-2.0，一行一条、RAG 友好；**数据集自述数据截止 2025-01-01** |
| ModelScope `Panda233/china_legal_articles_samples` | 201 部法律的 1000 条 / 9.4 万字 | Apache-2.0，只补全文集里缺的法律（按法律名去重，另有 58 部重复已跳过） |
| 国家法律法规数据库 `flk.npc.gov.cn` | 2 部 / 172 条 / 2.3 万字 | 官方 docx 正文解析（个保法、劳动合同法），带公布/施行日期与详情页链接，属**现行文本** |

> 为什么用公开语料而不是自己编：引用溯源必须能点到真实原文，文档里写的每个数字也要能被复现。
> 法条截止日期的风险会随答案一起输出（`generate.version_caveat`），不假装它是现行版本。

```
python scripts/fetch_corpus.py                 # gov.cn 抓取
python scripts/import_chinese_laws.py          # 177 部法律全文 → data/raw/law_*.txt
python scripts/import_ms_dataset.py            # 补缺：只导入全文集里没有的法律
python scripts/fetch_flk.py 个人信息保护法      # 官方站抓现行全文 → data/raw/flk_*.txt
python scripts/build_index.py                  # 切块 + 向量 + BM25 → data/index/
python scripts/search_demo.py                  # 稠密/稀疏/混合三路 Top-3 对比
python scripts/ask.py "问题"                    # 检索 → 本地 3B 生成 → 引用与拒答
python scripts/validate_qa.py                  # 校验评测集 gold 标注（法名/条号/关键词）
python scripts/run_eval.py                     # 检索层 Recall@5 / MRR 三配置 A/B
python scripts/run_eval.py --gen 40 --out eval/eval_full.json   # 加跑生成层（约 10 分钟）
python scripts/init_db.py --username admin          # M5：建 schema/表/账号（幂等，密码走 getpass）
python -m uvicorn kbra.api:app --app-dir src --port 8000   # M5：起服务（单进程，显存约 6GB）
python -m pytest -q                                             # 引用校验与评测打分单测
```

### M1 / M2 结果

- 400 篇 → **15993 块**（平均 134 字，最大 599 字），向量 512 维，建索引 **44.1s**
  - 分来源：法律全文/样本 15422 块、flk 官方文本 172 块、gov.cn 政策 399 块
- 单条查询（模型已 warm）**约 0.02s**；首条 13s 是 jieba 词典与模型加载的一次性开销
- 切块踩到的坑：法条行内会出现「依照本法第二百三十四条」这类**条内引用**，
  按「第X条」正则硬切会把《民法典》撕成 2582 个碎片（平均 67 字）。
  改为「一行即一条」并以行首条号判定，民法典回到 1260 块 = 实际条数

### M3 结果（2026-09-24 实跑）

- 检索命中正常：问「关键信息基础设施的个人信息要存在哪里」→ 正确引《网络安全法》第三十七条，
  答「应当在境内存储 [2]」
- 语料补全后：问「个人信息跨境提供需要满足什么条件」→ Top-4 全是《个人信息保护法》
  （第三十八/三十六条），答案准确列出出境的四种合法路径，引用全部指向 flk 官方原文链接
- 语料外问题（「2026 年世界杯冠军」）由**模型自己**判定资料不足、输出 `【无法回答】` 而拒答
- 代码里还有第二条拒答路径：首条稠密相似度 < `MIN_SIM=0.30` 时在**调用模型前**直接拒答，
  省一次推理。但 M5 复测时它**一次都没触发过**——中文负例 top1_sim 最低 0.406，
  连英文问题和 SQL 语法问题都有 0.419 / 0.420（bge 的相似度整体偏高）。
  已由 `tests/test_refusal.py` 锁住该路径的行为，并记下：阈值若上调到 0.45 以上，
  负例会在模型判断前就被拦掉，**M4 的拒答率需重测**
- 生成速度：M3 那轮 8–11 tok/s，M5/M6 走 HTTP 复测 **12.1–15.1 tok/s**
  （同一台机、同一模型；此前 `check_llm` 测到的 4.7 tok/s 是 756 token 长 prompt 的情况）
  ，单次问答 18–152 token ≈ 1.3–16s；8GB 显存够用
- **引用一致性校验抓到的真实错误**：问「个人数据出境需要安全评估吗」时，模型把
  「第三十七条」挂到《数据安全法》名下，而资料里《数据安全法》只有第三十/三十一/三十五条，
  第三十七条属《网络安全法》。校验按「法名 ↔ 条号」配对核，命中并打印
  `⚠️ 引用错配：《中华人民共和国数据安全法》被引作第三十七条…`。
  这是 3B 本地模型的典型失误，也是 M4 要量化的「幻觉率」指标之一
  - 该检查只认**紧贴法名**的条号串，因此答案里「依照本法第四十条」这种法条交叉引用
    （属正确内容）不会被误报，已用正则用例验证

### M4 评测结果（2026-09-24 实跑，`python scripts/run_eval.py --gen 40`）

评测集 `eval/qa_set.jsonl`：**117 题 = 103 正例 + 14 负例**。gold 只标「法律名 + 条号」
（如 `中华人民共和国劳动合同法 / 第十九条`），跑评测时再解析成 chunk_id，
所以换语料重编号也不用重标；`scripts/validate_qa.py` 会校验每条标注都能解析到真实块、
且预设答案关键词确实出现在该块正文里（出题时靠 `scripts/eval_seed.py` 抽含事实点的条文）。

检索层（103 题，Recall@5，同一份 15993 块索引）：

| 配置 | Recall@5 | MRR | 未命中 |
|---|---|---|---|
| 仅稠密向量（bge-small） | 0.961 | 0.888 | 4 |
| 仅 BM25（jieba 分词） | 0.981 | 0.900 | 2 |
| 混合 RRF（candidate=20/40 结果相同） | 0.981 | 0.910 | 2 |
| 混合 + BGE 重排 | 0.981 | **0.930** | 2 |

- 法条语料上 **BM25 略强于纯向量**：条号、法律名是很强的字面信号，稠密向量反而会漂
- RRF 融合消掉了两路各自独有的错（稠密 4 漏 → 混合 2 漏），MRR 随配置单调上升
- **重排不提 Recall、只提 MRR（0.910→0.930）**：它只能重排候选池内的块。
  剩下的 2 题（著作权法第五条「本法不适用于…」、食品安全法第六十三条「召回」）
  压根没进 20 条候选池，实测重排后 Top-5 仍不含 gold → 要提 Recall 得动召回，不是动排序

生成层（40 道正例等距抽样 + 全部 14 道负例，k=4，本地 Qwen2.5-3B-Instruct fp16）：

| 指标 | 值 | 口径 |
|---|---|---|
| 回答带 `[n]` 引用 | 0.925 | 37/40 正例 |
| 引用命中 gold 条文 | 1.000 | 带引用的回答里 |
| 凭空引用法律名 | 0.000 | 答案提到、检索材料里没有 |
| 法名↔编号 / 条号错配 | 0.000 | 校验器判出 |
| 负例正确拒答 | 0.929 | 13/14 |
| 检索层 k=4 命中 gold | 1.000 | 抽样 40 题 |

- 唯一没拒答的负例是「公司年假可以折算成几天调休给员工吗」——模型用带薪年休假的一般规则
  硬答了企业内部政策问题，属**该拒未拒**，是下一步要收的口子
- 第一版校验器报的「幻觉率 0.05」经复核**是假阳性**：① 同一部法律占多个资料编号时只记了
  最后一个下标；② 模型写「第35条」而语料写「第三十五条」，按字面比就误判。
  两处都已修（改成「名→编号列表」+ 条号归一化比较），并补了 11 条单测锁住行为，
  含「交叉引用不误报」「阿拉伯数字条号不误报」两个反例
- 耗时：检索层 4 配置全跑 67.5s（103×4 次编码），生成层 40+14 题约 9 分钟

### 已量化：措辞敏感导致的召回缺口

同一个知识点，问法换个词名次就差一截（以《个人信息保护法》第三十八条为例）：

| 问法 | 个保法第三十八条 名次 |
|---|---|
| 个人信息跨境提供需要满足什么条件 | **1** |
| 个人数据出境需要安全评估吗 | 17（挤进 20 条候选池，但不在 Top-5） |

第二问实际命中的是《数据安全法》第三十一条（出境数据安全管理），所以生成层会答得
「像对但引错法」。根因在**检索侧的词面不匹配**（数据 ↔ 信息），重排救不了。
候选解法：查询改写（同义词表 / LLM 扩写）、按标题+条号做二次索引、加大候选池。

## M5 服务化实测（2026-09-24）

代码：`src/kbra/api.py`（路由）+ `src/kbra/db.py`（SQLAlchemy 模型与鉴权）+
`scripts/init_db.py`（建库建表建账号，幂等）。DSN 从 `.env` 的 `KBRA_MYSQL_DSN` 读，
`config.py` 启动时 `load_dotenv`，代码库里没有任何明文密码。

### 接口

| 方法路径 | 鉴权 | 说明 |
|---|---|---|
| `GET /health` | 无 | 只报存活与索引是否已加载，不触发模型加载 |
| `POST /api/register` | 无 | 用户名正则 + 密码 8–64 位（bcrypt 上限 72 字节） |
| `POST /api/login` | 无 | 返回不透明 Bearer 令牌，库里只存 SHA-256；默认 72h 过期 |
| `POST /api/logout` | Bearer | 删令牌行，旧令牌立即失效 |
| `POST /api/ask` | Bearer | `{question,k,debug}` → 答案 + 引用 + `warnings`（分 `suspicious`/`mismatched`）+ `caveat` + 统计，**每次问答落一行 `qa_log`** |
| `GET /api/logs/{id}` | Bearer | 单条记录完整回读（含入模原文），前端历史回看用；别人的记录 404 |
| `POST /api/feedback` | Bearer | 点赞/点踩（`rating ∈ {1,-1}`），同一条记录重复提交为覆盖 |
| `GET /api/history` | Bearer | 自己的问答记录（带引用与反馈），`limit` 夹在 1–100 |
| `GET /`、`/app.js`… | 无 | `web/` 由 `StaticFiles` 挂在 `/`，页面与接口同源（M6） |

### 表（`scripts/init_db.py` 建；已在 MySQL 8.0.46 上实建，四表均 InnoDB / utf8mb4_0900_ai_ci）

- `user`：`username` 唯一、`pw_hash`（bcrypt 自带盐）
- `auth_token`：`token_hash` 主键、`expires_at` 索引
- `qa_log`：问题/答案/是否拒答/`top1_sim`/`citations`/`retrieved`（含原文截断 500 字）/`stats`/`warnings`/`caveat` + 时间索引
- `feedback`：`log_id` 唯一（一条问答最多一个反馈）

### 真实跑通记录（首轮，当时数据落在临时 sqlite）

`uvicorn kbra.api:app`（单进程；模型常驻 6GB 显存，多 worker 会各占一份），
真索引 15993 块 + 真 Qwen2.5-3B 生成，HTTP 客户端实测：

| 场景 | 实测 |
|---|---|
| 首条 `/api/ask`（含索引与模型加载） | 37.6s |
| 热态 `/api/ask`（k=4，81 tok） | 4.7s / 8.8s，生成 **9.71 tok/s**，`retrieve_s` 2.28s |
| 语料外问题「2026 年世界杯冠军是谁」 | 1.7s，`refused=true`（**由模型输出 `【无法回答】`**；`top1_sim 0.406` 高于 `MIN_SIM 0.30`，没走低相似度短路） |
| 无令牌 / 乱填令牌 / 登出后旧令牌 | 全部 401 |
| 密码错 vs 用户不存在 | 都是 401 同一句文案，不泄露用户是否存在 |
| 给别人的 `log_id` 提交反馈 | 404 |

冒烟时抓到并修掉一个真问题：3B 模型会把 Prompt 的段头复读成答案开头
（`【资料】[1][2]\n根据《中华人民共和国劳动合同法》…`）。修法是**只清显示文本**
（`generate.ECHO_RE`），引用解析仍在原文上做——否则这行的 `[1][2]` 会被从溯源结果里丢掉。
`tests/test_citations.py` 用这条真实输出锁住两个方向（该清的清掉、正常的不误删）。

单测：`python -m pytest -q` → 当时 **26 passed**（M7 压测后增至 27；`tests/test_api.py` 用
`dependency_overrides`
替换索引与模型、生成函数打桩，跑同一套元数据建的临时 sqlite，不需要 GPU 也不需要 MySQL；
`tests/test_refusal.py` 锁住低相似度短路那条路径）。

### MySQL 真库复跑（2026-09-24，专属实例 3308）

上面那轮把 DSN 指向临时 sqlite。之后另起了本项目专属的 **MySQL 8.0.46** 实例
（`E:\DevEnv\mysql-kbra`，端口 3308，见「本机环境事实」），**只改 `.env` 里
`KBRA_MYSQL_DSN` 一行、代码未动**，同一套流程重放：

| 检查项 | 实测结果 |
|---|---|
| `python scripts/init_db.py` | 建库 + 四表就绪，首跑回显「已创建用户 demo」；**重复执行实测幂等**（第二次回显「用户 demo 已存在（id=1），未改密码」，不会重置密码） |
| `SHOW CREATE TABLE qa_log` | InnoDB / utf8mb4 / `utf8mb4_0900_ai_ci`，`citations`+`retrieved`+`stats`+`warnings` 均为原生 **JSON** 列，`user_id` 外键 + `user_id`/`created_at` 两个索引 ✅ |
| 注册 → 登录 → `POST /api/ask` | 201 / 200；首条 32.2s（含索引与模型加载），`top1_sim 0.701`，检索 1.928s，生成 61 tok / 5.02s = **12.14 tok/s**（数字取自 `qa_log.stats` JSON 列回读，非手记） |
| 中文往返 | `question/answer/caveat/feedback.comment` 写入后按 utf8mb4 原样回读，无乱码；`JSON_EXTRACT` 在库内可直接取 `stats` 与 `citations` |
| 引用一致性校验 | 真实抓到模型把《数据安全法》引作「第三十七条」（本轮入模资料里该法只有第 30/31/35 条），落进 `warnings.mismatched`，前端出红标 ✅ |
| 反馈 | `rating=-1` + 中文备注入库；`log_id` 唯一约束在 MySQL 上生效（重复提交为覆盖，不产生第二行） |
| `GET /api/logs/{id}` | 与当初 `POST /api/ask` 的返回逐字段一致（JSON 列回读无字段丢失） |
| 登出 | 200 → 旧令牌立刻 401，`SELECT COUNT(*) FROM auth_token` = 0（真删行，不是软标记） |

补测（同一进程热态，HTTP 直连 `:8020`，每题计时取 `stats` 列）：

| 问题 | 端到端 | 检索 | 生成 | tok/s |
|---|---|---|---|---|
| 关键信息基础设施的个人信息要存在哪里 | 3.89s | 0.075s | 3.80s / 53 tok | 13.94 |
| 同上（再问一次） | 3.71s | 0.067s | 3.61s / 53 tok | 14.68 |
| 同上（第三次） | 3.59s | 0.062s | 3.51s / 53 tok | 15.12 |
| 2026 年世界杯冠军是谁（语料外） | 1.35s | 0.046s | 1.29s / 18 tok | 13.97 |
| 英文 Kubernetes 配置问题（语料外） | 1.85s | 0.084s | 1.75s / 21 tok | 12.01 |
| PostgreSQL `OFFSET/LIMIT` 用法（语料外） | 1.35s | 0.064s | 1.27s / 19 tok | 14.93 |

三个语料外问题的 `top1_sim` 分别是 0.406 / 0.419 / 0.420，**都高于 `MIN_SIM=0.30`**，
所以拒答全部是模型输出 `【无法回答】` 那条路，低相似度短路一次都没触发
（连英文与 SQL 问题都有 0.42 的相似度，bge 在这个语料上整体偏高）。
这既说明短路省下的那次推理在实际负例上并没有省到，也说明**动这个阈值前必须重跑 M4**——
已写进 `tests/test_refusal.py` 的用例说明里。

耗时差异来自索引/模型的加载顺序，与数据库无关；M7 压测也**没有单独测过**MySQL 的写入耗时
（100 并发下 `读库` 慢的成因见 M7 那段：那条目前仍是**推断**，验证复测还没做），
所以这里不写「数据库不再是瓶颈」这类没有数字支撑的结论。

> 冒烟数据当时已清理成**四张空表、零账号**（含首次建的 `demo`——它的密码
> 只在当时那条命令行里出现过，没进任何文件，留着反而没人能用）。
> 要用就 `python scripts/init_db.py --username <自己的名字>`，或者直接在前端点「注册」。
>
> 2026-09-25 现状更新：为了复跑 M7 那一轮，库里现有一个 `loadtest` 账号和它产生的
> **2 条 `qa_log`**（就是上面「第三处修复」那两次被污染的单次请求，留着当证据）。
> 密码同样只在命令行里出现过，没进任何文件；不需要了就删账号。

## M6 前端实测（2026-09-24，浏览器真机走查）

`web/index.html` + `web/style.css` + `web/app.js`（约 250 行，无构建链、无框架），
由 `src/kbra/api.py` 末尾的 `StaticFiles` 挂在 `/` → **页面与接口同源，不需要 CORS 配置**。

浏览器（Playwright）实测的黄金路径与边界：

| 检查项 | 结果 |
|---|---|
| 注册/登录 → 令牌存 `localStorage`，刷新页面自动恢复会话 | ✅ 刷新后 `#app` 直接可见，历史 3 条 |
| 提问 → 答案 | ✅ 端到端 39.5s（冷启动含索引与模型加载）；热态同问题在 MySQL 那轮复测 **3.6–3.9s**（见 M5 补测表）。首轮笔记里那个「1.9s」与拒答耗时同量级、复现不出来，以复测数字为准 |
| 引用展开 | ✅ `[1]《中华人民共和国劳动合同法》 第十九条` 点开是原文 + `flk.npc.gov.cn` 出处链接 + 许可证 |
| 点赞/点踩 | ✅ 按钮变色 + 「已反馈：有用」，历史记录行尾出现 👍；刷新后状态从库里读回 |
| 历史回看 | ✅ 点历史条目走 `GET /api/logs/{id}`，答案/引用/入模原文/统计/反馈全部复原 |
| 检索调试面板 | ✅ 四行候选：名次、`chunk_id`（如 `flk_02#18`）、法名+条号、RRF 分、点开看原文 |
| 拒答路径 | ✅ 语料外问题显示「已拒答」徽标 + `top1 相似度 0.406 ｜ 生成 1.47s / 18 tok` |
| 控制台 | ✅ 干净（修掉了 favicon 404） |

走查中发现并修掉的两处：

1. **`.env` 还没建时（DSN 为空）会让 uvicorn worker 直接退出** → `get_db` 现在捕获
   `SystemExit` 返回 503 与可执行的提示文案；`/health` 不依赖数据库仍返回 200。
   已按「新克隆仓库」的真实状态起服务核对：`POST /api/login` → `503 缺少 KBRA_MYSQL_DSN…`。
2. 提交后输入框不清空，回车两次就写成两条同样的问答记录 → 成功后清空。

另外接口在 M6 补了两处内容（都是前端要、而 M5 没返回的）：
`retrieved` 里带 `text`（截断 500 字）供「引用展开」用；`caveat` 字段把
**法条数据集截止 2025-01-01** 这句风险提示随答案一起给（`qa_log` 新增 `caveat` 列）。

上表的首轮走查跑在临时 sqlite 上。库换成 MySQL 3308 后**同一条黄金路径在浏览器里重放过**：
登录 `demo` → 提问「关键信息基础设施的个人信息要存在哪里」→ 端到端 **4.0s**
（检索 0.207s + 生成 53 tok / 3.73s = **14.22 tok/s**，`top1_sim 0.725`，
数字与 `qa_log.id=2` 的 `stats` JSON 列一致）；引用 `[2]《中华人民共和国个人信息保护法》
第四十条` + `[3]《中华人民共和国网络安全法》第三十七条`，caveat 正常显示，
提交后输入框已清空（历史仍为 1 条，未重复写入）。

## M7 压测实测（2026-09-24，locust 2.46.6）

链路：真索引 15993 块 + 真 Qwen2.5-3B + MySQL 3308，单进程 uvicorn，
locust 无界面按 9:1 的权重混打 `生成`（`POST /api/ask`）与 `读库`（`GET /api/history`）。
入口 `scripts/locustfile.py`，CSV 落在 `locust_report/`（已 gitignore）。

**压测一上来就抓到一个真 bug：20 并发时 `/api/ask` 100% 返回 500。**

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00
```

原因不在 GPU：`/api/ask` 用了请求级 Session（`Depends(get_db)`），
而一次生成要跑几秒到几十秒 → **数据库连接在整个生成期间被白白占着**。
15 个连接槽位被 20 个并发占满，后来的请求排队 30s 后超时抛 500
（连 `GET /api/history` 都被拖到 30s 失败）。

修法是让慢操作不持有连接：新增 `get_session_factory` 依赖，令牌读取与日志写入
各开一个毫秒级短会话，`/api/ask` 不再依赖请求级会话；`tests/test_api.py` 里
用「把 `get_db` 换成一个被调用就抛断言的实现」锁住这个回归 → **27 passed**。

第二处是显存：单卡 8GB 上并行跑 `model.generate` 只会互相拖慢，聚合吞吐一点没涨，
显存还顶到 7.8/8.2GB。所以加了 `GEN_LOCK` 把生成串行化。（这把锁后来被 `GEN_POOL`
取代，见下面「第三处修复」。）

修复前后实测（P95 取 locust 打印的分位，端到端含 HTTP 与落库）：

| 并发 | 修复前 | 修复后 |
|---|---|---|
| 1 | 单次 5.4–10.0s，0 失败 | — |
| 5 | 单次 `generate_s` 17.3–18.6s（2.9 tok/s），0 失败 | — |
| 10 | 单次 49–52.7s（P50 49s），0 失败；`读库` 仍 15ms | — |
| 20 | **`生成` 4/4 全部 HTTP 500，`读库` 1/2 失败** | 60s→3min：**0 失败**，`生成` avg 73.4s / P50 91s / **P95 106s**，`读库` avg 96ms |
| 100 | （没测，20 就已经 500） | 5min：**0 失败、无 CUDA OOM**（显存 7.69/8.19GB），`生成` avg 198s / P50 202s / **P95 289s** |

**吞吐与瓶颈的真实结论（不粉饰）**：`/api/ask` 的聚合吞吐修前修后都稳定在
**0.13–0.19 req/s**——一张卡串行生成就是这个上限，加并发只加排队深度。
100 并发下 P95 = 289s 就是这么来的；同一轮里 `登录` 100 次仍只要 avg 1.1s，
但 `读库` 被拖到 avg 31.3s / P50 50s。两者都只碰 MySQL，差别在于 `读库` 排在
生成请求后面——**推断**是 40 个 anyio worker 线程大多卡在 `GEN_LOCK` 上，
即**慢接口把整个 API 的线程池占住了**（这条是推断，不是直接测出来的：
要证实得看线程占用或把 `读库` 拆到独立进程再压一轮）。下一步也就在这儿：
生成走独立 worker/队列，或多实例 + 反向代理——多实例正是容器化那块，本轮按用户决定暂缓。

> 「100 并发 P95」这个指标的实测口径：**单实例单卡的 P95 是 289s，
> 不是几十毫秒**。按实测 0.19 QPS/实例折算，要撑住 1 QPS 就得 ~5 个带卡实例
> （或把生成挪到独立 worker 池 + 批处理），这比写一个没测过的数字有用。

### 第三处修复：生成移出请求线程（代码已改，对照复测**未做**）

上面那条推断要证实，只有一条路：把生成从请求线程里挪出去，再压同一级并发看 `读库`
掉不回毫秒就是猜错了。改动已经落地——

- `/api/ask` 改成 `async def`，生成提交给单 worker 的 `GEN_POOL`
  （`concurrent.futures.ThreadPoolExecutor(max_workers=1)`），`GEN_LOCK` 删掉：
  一个 worker 本身就是串行，聚合吞吐与显存保护都不丢。
- 排队因此发生在 `GEN_POOL` 的队列里，而不是 anyio 那 40 个 worker 线程上；
  等待生成的请求也不持数据库连接（短会话那条不变）。
- `tests/test_api.py` 新增一条：打桩函数里记录 `threading.current_thread().name`，
  断言生成确实跑在 `gen*` 线程 → **28 passed**（原 27 条全绿）。

**对照数据还没有，且原因必须写清楚：复测环境不干净，测出来的数字不可信。**
2026-09-25 凌晨准备复跑 `u=100` 时，本机另有程序占卡（`CODM.exe` ≈ 1.1–2.1GB，
加上 `dwm` 1.7GB），加载模型后显存只剩 **34/8192 MiB**。同一问题（`关键信息基础设施的
个人信息要存在哪里`，`top1_sim 0.725`、53 tok）的热态单次实测：

| 状态 | `retrieve_s` | `generate_s` | tok/s |
|---|---|---|---|
| 基线（2026-09-24，机器空闲时） | 0.207s | 3.73s | **14.22** |
| 复测当时（有程序占卡） | 0.09s | 47.6s / 51.6s | **1.03–1.11** |

差了 **12 倍**，全部来自 `generate_s`（检索反而更快，CPU 侧没问题）——显存被挤到 34MiB
后走 WDDM 系统内存回退。在这种条件下压 u=100，`生成` 与 `读库` 的延迟都由这个因素
决定，**改动到底有没有把读库拉回毫秒根本读不出来**。所以这一轮按「环境不满足」
中止，服务已停（显存回到 1.8GB 占用），不拿污染数字替换基线。

复跑清单（机器空出来之后，两分钟的事）：

```
# 1) 确认基线条件：显存空闲 ≥7GB，无其它占卡程序
nvidia-smi --query-gpu=memory.used,memory.free --format=csv
# 2) 起服务 + 建压测账号（3308 实例需已启动）
python -m uvicorn kbra.api:app --app-dir src --host 127.0.0.1 --port 8000
python scripts/init_db.py --username loadtest
# 3) 同一级并发复跑，CSV 换名字别覆盖基线
set KBRA_LOAD_USER=loadtest && set KBRA_LOAD_PW=<压测账号密码>
python -m locust -f scripts/locustfile.py --headless -u 100 -r 50 -t 5m \
    --csv locust_report/async_u100 --host http://127.0.0.1:8000
```

看 `async_u100_stats.csv` 里 `读库` 一行的 50%ile：回到 ms 量级（基线 50s）就证实那条
推断，同时 `生成` 的 avg/P95 应与基线 198s/289s 同量级（聚合吞吐不会变，卡就一张）；
若 `读库` 仍是几十秒，说明瓶颈不在 anyio 线程池，得回头看 MySQL 连接或 GIL。

## 部署状态（M7 未完成的部分）

- ✅ 压测与压测暴露的两个缺陷修复
- ⏸ 容器化：用户 2026-09-24 决定「先压测，Docker 以后再说」→ 本机未装 Docker Desktop，
  `Dockerfile` / `docker-compose.yml` 尚未编写，本文档不把容器化算作已完成项
- 复现步骤：`python -m uvicorn kbra.api:app --app-dir src --port 8000` 起服务，
  先用 `scripts/init_db.py --username loadtest` 建压测账号，再
  `python -m locust -f scripts/locustfile.py --headless -u 100 -r 50 -t 5m --host http://127.0.0.1:8000`

## 目录结构（与磁盘一致）

```
kb-rag-assistant/
  src/kbra/config.py       路径与 DSN（.env 加载）
  src/kbra/chunking.py     按结构边界切块
  src/kbra/embeddings.py   bge 稠密向量编码
  src/kbra/index.py        向量 + jieba/BM25 + RRF 融合检索
  src/kbra/rerank.py       BGE 交叉编码器重排
  src/kbra/generate.py     Prompt 组装、引用溯源、拒答、一致性校验
  src/kbra/evaluate.py     gold 解析与 Recall/MRR 打分
  src/kbra/db.py           用户/令牌/问答日志/反馈 + 密码与令牌哈希
  src/kbra/api.py          FastAPI 路由（M5）
  scripts/                 入库、抓模型、检索演示、问答、评测、建库、压测入口
  data/raw/                语料原文 + manifest.json（出处与许可证）
  data/chunks.jsonl        切块产物   data/index/  向量与 BM25 索引（不入库）
  eval/qa_set.jsonl        117 题评测集；eval/*.json 为指标落盘结果
  tests/                   引用校验、段头清理、接口与鉴权单测
  web/index.html  web/style.css  web/app.js   M6 前端（同源挂载，无框架无构建链）
  .env.example   配置模板（全部占位）    .env  本机真实配置，已 gitignore，不入库
```
