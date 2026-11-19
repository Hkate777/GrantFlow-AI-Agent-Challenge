# 架构设计说明

## 1. 设计目标

场景 16 的核心挑战：**各计划要求、格式、准则各异；材料结构化与非结构化混杂；需支持评审、决策与后续管理全流程**。GrantFlow 的三条设计原则：

1. **规则外置**：计划差异收敛到 YAML 声明式配置（`configs/`），代码零改动适配新计划。
2. **人机协同**：Agent 只做「检查与建议」，淘汰/回避/复议等关键决策必须人工确认（`needs_human` 标记贯穿全链路）。
3. **可复现可审计**：MockLLM 保证离线确定性；AuditTrail 记录每个动作与依据。

## 2. 模块职责

| 模块 | 职责 | 关键设计 |
|---|---|---|
| `ingest.py` | 多格式解析 | 每种格式独立解析器；依赖可选、失败不中断（`error` 字段） |
| `extract.py` | LLM 结构化抽取 | JSON 输出 + 解析容错降级；Mock/真实 LLM 同接口 |
| `check.py` | 完整性/合规审查 | 规则引擎按 YAML 执行；利益冲突用「评审人→关键词」映射 |
| `extract.py::cross_verify` | 跨源比对 | 数值容差 5%；标题/邮箱双向包含匹配 |
| `extract.py::anomaly_scan` | 批次异常 | 中位数 ± 5×MAD 稳健离群检测 |
| `summarize.py` | 摘要与资料包 | 模板化 + LLM 凝练；输出 summary/one_pager/checklist |
| `review.py` | 评审汇总 | 维度归一百分制加权；总分差 ≥ 阈值触发复议 |
| `track.py` | 项目追踪 | 逾期/临期（14 天）两级预警；资助发放进度表 |
| `pipeline.py` | 编排 | 阶段式 Agentic Workflow，全程 AuditTrail |
| `llm.py` | LLM 抽象 | Protocol 接口；Mock（离线启发式）/ OpenAI 兼容端点 |

## 3. 数据流

申请目录（meta.json + 附件）→ `load_applications` → `Application(docs=[Document...])`
→ intake 产出 `extracted: dict` + `findings: [Finding]`
→ build_packs 产出评审资料包目录
→ review 消费 `ReviewScore[]` 产出加权结果/通知/待办
→ track 消费 `Milestone[]` 产出管理报告

所有中间产物落盘（Markdown/JSON），天然支持「断点续跑」与人工介入。

## 4. 扩展点

- **新格式**：在 `ingest._EXT_MAP` + 新增解析函数即可。
- **新计划**：复制 `configs/program_demo.yaml` 改配置。
- **新规则**：`check_completeness` 为纯函数，便于单测与扩展。
- **MCP 封装**：各 Agent 均为无状态纯函数，可直接包为 MCP 工具供跨赛道复用。
