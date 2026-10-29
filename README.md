# DD-Agent · 智能尽职调查分析系统

> 面向酒店、文旅、贸易、投资等项目资料包的自动化尽调 MVP：自动盘点文件、分拣模块、跑规则引擎、跨口径交叉验证，输出带证据与计算留痕的尽调报告。**规则计算不经过 LLM，LLM 仅作为可选增强**；没有配置模型时系统仍可基于规则与模板完整运行。

---

## 1. 项目简介

DD-Agent 把一套尽调方法论固化为可执行的代码流水线：

- **输入**：一个项目资料文件夹（或 Web 端上传的 zip），内含财务报表、银行流水、纳税申报、合同、诉讼、资质、章程、征信、运营数据、OTA 协议等混合格式文件。
- **处理**：自动识别文件类型 → 按五大尽调模块分拣 → 提取结构化数据 → 红旗规则扫描 → 三口径交叉验证 → 事实抽取与风控分级 → 生成报告。
- **输出**：Markdown / HTML / Excel 三种格式报告，附带文件清单、事实表、计算底稿、风险证据归档与完整运行日志。

### 设计原则

| 原则 | 落地方式 |
|---|---|
| 规则优先、LLM 可选 | 财务计算、比率、红旗判定全部由确定性 Python 规则完成；LLM 只做文件归类辅助、条款总结、异常解释与报告润色，不可用时自动降级 |
| 全程留痕、可溯源 | 每个关键数字记录公式、输入、结果与来源文件（`calc_trace.json`）；风险结论附原始证据文件（`evidence/`） |
| 单模块失败不拖垮全局 | 每个分析步骤独立 try-catch，降级为 `skipped / warning / degraded / fatal`，流程仍能产出部分报告 |
| 行业知识外置 | 分类词、行业红旗、指标基准、报告章节、事实清单全部放在 `config/industries/*.yaml`，新增行业不改代码 |
| 容错与可解释 | 即使缺少标准财务报表，也会按已识别到的模块正常出报告，并在覆盖度章节说明资料缺口 |

---

## 2. 核心能力

### 2.1 五大尽调模块

| 模块 | 回答的问题 | 主要处理文件 |
|---|---|---|
| 财务真实性 | 报表能不能信、利润是否真实、钱是否收到 | 利润表、资产负债表、现金流量表、科目余额表、成本明细 |
| 业务可持续性 | 生意是否稳定、收入预测是否靠谱 | PMS / 景区运营数据、商业计划书、OTA 渠道协议、品牌与规划资料 |
| 法律合规 | 有没有或有负债、合同有没有坑 | 租赁合同、重大合同、未决诉讼、行政处罚、对外担保、土地文件 |
| 资质有效性 | 能否继续合法经营、证件是否过期 | 营业执照、特种行业许可、消防验收、卫生 / 食品 / 旅游资质 |
| 管理层与股东 | 股东与实控人背景、治理是否稳定 | 股东名册、股权架构、工商变更、征信报告、高管简历、公司章程 |

### 2.2 交叉验证（多口径对质）

- **收入三口径对比**：利润表收入 ↔ 纳税申报收入 ↔ 银行流水转入，输出逐月差异表。
- **成本真实性验证**：成本费用明细表加总与报表口径核对。
- **合同条款 vs 实际**：租金、押金、佣金率等从协议文本抽取后参与规则判定。
- **或有事项 vs 报表**：诉讼、担保、处罚单独归档，提示是否足额计提。

### 2.3 结论层与留痕产物

- **事实层 `facts.json`**：关键数字带来源文件、置信度与多源一致性比对。
- **风控结论 `verdict.json`**：五维加权评分（财务真实性 / 业务可持续性 / 法律合规 / 资质 / 治理）、风险等级、Top 风险、缺失资料清单与建议动作。
- **计算底稿 `calc_trace.json`**：比率公式、输入值、结果、来源文件逐项可复核。
- **证据归档 `evidence/`**：命中风险对应的原始文件复制到报告目录。

### 2.4 报告形态

- Markdown（可编辑源文件）、HTML（浏览器直接阅读、图表内嵌）、Excel（结构化明细）。
- 5 张标配图表：收入与净利润趋势、成本费用结构、资产负债趋势、毛利率走势、关键行业指标。
- 失败 / 降级任务也保留文件分类概览与日志，便于溯源排查。

---

## 3. 系统架构与运行流程

### 3.1 分层结构

```
┌─────────────────────────────────────────────────────────┐
│  接入层  CLI (click)  ·  Flask Web (登录/上传/进度/下载)    │
├─────────────────────────────────────────────────────────┤
│  编排层  dd_agent/orchestrator.py                          │
│  步骤状态追踪 · LLM 分类兜底 · 模块串联 · 事实/结论/留痕    │
├─────────────────────────────────────────────────────────┤
│  分析层  inventory / analyzer / redflag / crosscheck /     │
│          legal / qualification / management / operations / │
│          ota / ocr / extractor                             │
├─────────────────────────────────────────────────────────┤
│  知识层  config/red_flags.yaml  +  config/industries/*.yaml │
│  结果层  facts · verdict · trace · reporter(md/html/xlsx)  │
├─────────────────────────────────────────────────────────┤
│  外部能力  PaddleOCR（扫描件） · Ollama / OpenAI 兼容 API   │
└─────────────────────────────────────────────────────────┘
```

### 3.2 标准流水线（`DDAgent.run`）

1. **目录盘点**：遍历文件、识别格式、跳过噪声目录。
2. **三层文件分拣**：文件名关键词粗分 → 内容轻量校验（Excel 表头 / Word 段落 / 文字版 PDF 前几页）→ 轻量 OCR 兜底；视频一律归为未分类。
3. **LLM 归类与重要性打分**（可选，最多处理未分类与扫描件 6 个，失败回退关键词评分）。
4. **财务报表分析**：三表提取、口径对齐、比率计算、三层留痕。
5. **红旗规则扫描**：基础规则 + 行业规则，输出高 / 中 / 低风险命中。
6. **跨模块交叉验证**：收入三口径、成本结构、法律合同抽取。
7. **法律合规 / 资质核验 / 管理层股东 / 运营数据 / OTA 渠道**分模块处理。
8. **行业与主体规则补扫**：依赖前序模块结果的补充红旗。
9. **事实抽取 → 风控分级 → 证据归档 → 计算底稿 → 生成报告**；外层 `finally` 强制写入 `meta.json`，保证前端在成功或失败时都能读到状态、失败步骤与总耗时。

---

## 4. 目录结构

```
dd-agent/
├── dd_agent/                         # 核心 Python 包
│   ├── orchestrator.py               # 主编排器：串联全部步骤、状态追踪、写 meta
│   ├── cli.py                        # click 命令行入口：scan / inventory / portfolio / diff
│   ├── __main__.py                    # 支持 python -m dd_agent
│   ├── industry.py                   # 行业包加载器：YAML 父子 extends 合并、去重
│   ├── facts.py                      # 事实层：抽取、多源一致性、覆盖度、Markdown 输出
│   ├── verdict.py                    # 风控结论层：五维加权评分、等级、证据、建议
│   ├── trace.py                      # 计算底稿：公式 / 输入 / 结果 / 来源留痕
│   ├── importance.py                 # LLM 文件重要性 0-100 打分，失败回退关键词
│   ├── monitor.py                    # 多项目组合视图 portfolio + 两次运行对比 diff
│   ├── runtime_config.py            # settings/auth/secret_key 读写、密码哈希、会话版本
│   ├── visitor_config.py             # 访客隔离配置，API Key 用 Fernet 加密落盘
│   │
│   ├── inventory/scanner.py           # 三层文件分拣、优先级、重要性、人工纠正持久化
│   ├── analyzer/analyzer.py           # 三表提取、口径对齐、财务比率计算
│   ├── redflag/engine.py             # 纯 Python 确定性红旗规则引擎
│   ├── crosscheck/validator.py        # 纳税 / 流水 / 报表三口径对比 + 成本结构验证
│   ├── legal/analyzer.py             # 租赁合同 / 诉讼 / 处罚条款抽取，扫描件走 OCR
│   ├── qualification/checker.py      # 营业执照与许可证有效期、经营范围核验
│   ├── management/analyzer.py         # 股东 / 征信 / 工商变更 / 公司章程条款
│   ├── operations/analyzer.py         # PMS 运营指标：出租率 / ADR / RevPAR
│   ├── operations/ota_analyzer.py     # OTA 佣金率、账期、独家条款抽取
│   ├── extractor/readers.py           # Excel / PDF / Word 通用读取器
│   ├── extractor/doc_convert.py       # 老版 .doc → .docx（LibreOffice / Word COM，MD5 缓存）
│   ├── ocr/extractor.py              # 文字版 PDF 直读优先；PaddleOCR 单例 + MD5 缓存 + 按页提取
│   ├── ocr/numpy_compat.py           # numpy ABI 兼容补丁
│   ├── llm/client.py                  # Ollama / OpenAI 兼容 API 双模式，无 Key 自动降级
│   │
│   └── reporter/
│       ├── generator.py              # 报告组装：MD / HTML / Excel，分章节构建
│       ├── charts.py                 # 5 张标配图表（中文字体跨平台适配）
│       └── docx_export.py            # Markdown → Word 导出
│
├── web/                              # Flask Web MVP
│   ├── app.py                        # 登录守卫、上传解压、异步子进程、进度/日志/历史/下载/删除
│   └── templates/                    # index / login / progress / report / settings / my 页面
│
├── config/
│   ├── settings.yaml                 # 全局阈值、模块权重、输出格式
│   ├── red_flags.yaml                # 基础红旗规则库
│   └── industries/                   # 行业知识包（extends 继承 generic）
│       ├── generic.yaml              # 通用底座：维度权重、必备资料、事实清单、章节
│       ├── hotel.yaml                 # 酒店 / 民宿 / 度假村
│       ├── culture-tourism.yaml       # 景区 / 文旅小镇 / 旅行社
│       ├── investment.yaml            # 股权投资 / 并购 / 基金
│       └── agency-procurement.yaml    # 供应链 / 贸易 / 代理采购
│
├── tests/                            # 冒烟与端到端测试（详见第 8 节说明）
├── requirements.txt
├── pyproject.toml
├── Dockerfile / docker-compose.yml / Caddyfile / wsgi.py
├── DEPLOY.md                         # 服务器 Docker + Caddy 自动 HTTPS 部署指南
└── .env.example                      # 环境变量模板（真实 .env 不入库）
```

---

## 5. 快速开始

### 5.1 环境要求

- Python 3.10 及以上
- 扫描件 OCR 需要 PaddleOCR（安装时连带 PaddlePaddle，体积较大）；纯文字版文档不依赖 OCR 也能运行
- LLM 为可选项：本机 Ollama，或任意 OpenAI 兼容接口（DeepSeek、通义、硅基流动、OpenAI 等）

### 5.2 安装

```bash
git clone <你的仓库地址>
cd dd-agent

python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
```

### 5.3 配置

```bash
cp .env.example .env
# 按需填写：管理员初始密码、访客密码、LLM 提供商 / 地址 / 模型 / Key
```

首次启动会在 `data/` 自动生成 `settings.json`、`auth.json`、`secret_key`；这些文件属于本机运行状态，均已在 `.gitignore` 中排除。

### 5.4 命令行使用

```bash
# 完整分析并生成报告
python -m dd_agent scan "路径/到/项目资料文件夹" -o ./output --industry hotel

# 只做文件盘点与分拣，不出报告
python -m dd_agent inventory "路径/到/项目资料文件夹" --industry generic

# 多项目组合视图
python -m dd_agent portfolio web/results

# 同一项目两次运行对比
python -m dd_agent diff web/results/task_001 web/results/task_002
```

### 5.5 Web 界面（本地调试）

```bash
python -m web.app
# 浏览器访问 http://127.0.0.1:5000
```

- 管理员密码可进入设置页：修改管理员 / 访客密码、切换 LLM 引擎、测试连接。
- 访客只能上传资料与查看报告，无权进入设置页；访客可在「我的模型」配置自己的 API Key（加密隔离落盘，不写全局配置）。
- 上传 zip 后后台异步执行，前端实时轮询步骤进度、预计耗时与完整日志；任务成功或失败都可点进详情查看分类结果、报告与日志。

---

## 6. 行业包扩展

新增一个行业只需在 `config/industries/` 复制现有 yaml 修改，无需改动 Python 代码：

- `categories`：文件名关键词与归属模块；
- `redflags`：行业特有红旗（触发表达式、阈值、解释模板）；
- `metrics` / `benchmarks`：行业指标与基准区间；
- `required_categories`：缺失资料检查清单；
- `dimensions`：评分维度与权重；
- `facts`：该行业需要抽取的事实及其来源文件类型。

子包通过 `extends: generic` 继承通用底座，列表自动追加、字典深合并、规则 id 去重。

---

## 7. 配置项（`.env`）

| 变量 | 说明 |
|---|---|
| `DD_DOMAIN` | 部署域名，Caddy 据此申请 HTTPS 证书 |
| `DD_INITIAL_PASSWORD` | 首次启动的管理员密码；留空则随机生成并打印到日志 |
| `DD_GUEST_PASSWORD` | 可选访客密码；留空即关闭访客登录 |
| `DD_MAX_UPLOAD_MB` | 上传 zip 大小上限（MB），Flask 与 Caddy 共用 |
| `DD_LLM_ENABLED` | `0` 关闭 LLM，全程走规则引擎 |
| `DD_LLM_PROVIDER` | `ollama` 或 `api` |
| `DD_LLM_BASE_URL` / `DD_LLM_MODEL` / `DD_LLM_API_KEY` | 云端 OpenAI 兼容接口配置 |
| `OLLAMA_BASE_URL` / `DD_LLM_MODEL` | 本机 Ollama 地址与模型名 |

> 网页设置页保存后以运行时配置为准；`DD_IGNORE_SETTINGS=1` 时跳过全局配置，使用环境变量（用于访客自带 Key 的临时任务，不落盘）。

---

## 8. 测试说明

- `tests/test_web_auth.py`：登录、权限隔离、设置保存的冒烟测试，不依赖本地真实资料，可在干净环境运行。
- 其余 `*_e2e.py`、`test_ocr*.py`、`test_import.py` 为开发期针对本地资料包的联调 / 调试脚本，内含本机绝对路径与真实样本，已在 `.gitignore` 中排除、不随仓库分发；贡献时建议改造为 `pytest` + 合成 fixture。

运行可移植测试：

```bash
python -m pytest tests/test_web_auth.py
```

---

## 9. 部署

服务器 Docker 部署（含 Caddy 自动 HTTPS、数据卷持久化、管理员 / 访客权限）见 [DEPLOY.md](./DEPLOY.md)。

---

## 10. 安全与免责声明

- 本系统为**尽调辅助工具**，输出结论不构成投资、法律或审计意见；正式决策需由持证专业人员复核。
- 上传的财务与合同资料属于敏感数据：使用云端 API 时资料会发送至接口方，请先确认合规要求；对资料保密性要求高时应使用本地 Ollama 模型。
- 当前版本为单机演示系统，未实现多租户权限隔离、任务队列与审计日志，不建议直接对公网开放匿名注册。
- 仓库不包含任何真实企业资料、`.env`、密码哈希、API Key、历史报告与缓存；首次部署后请立即修改初始管理员密码。

## 11. 已知限制与路线图

- 老版二进制 `.doc` 在缺少 LibreOffice / Office 时无法转换，会在报告中提示。
- 纯扫描、无文字层且 OCR 未覆盖的表格，结构化提取精度有限。
- 规则库目前覆盖通用、酒店、文旅、投资、代理采购五类行业，其他行业需补充对应行业包。
- 后续计划：合成数据驱动的单元测试、多任务并发队列、更细粒度的 OCR 分页目录索引、行业基准库扩充。

## 12. 许可证

本项目基于 [MIT License](./LICENSE) 开源，可自由使用、修改与分发，需保留原始版权声明。第三方依赖（Flask、pandas、PaddleOCR、matplotlib 等）遵循各自开源许可证。
