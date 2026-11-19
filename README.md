# GrantFlow · 申請評審與項目管理流程自動化 Agent

> 「智賦香江」AI 智能體應用大賽 · 指定賽道 · **場景 16**（教育與文化創意賽道 · 校內行政工作流方向）
> 參賽隊伍：**KK_Garden**（2 人）

GrantFlow 面向大學、科技園、創投機構及創新創業支援單位，把「申請受理 → 資格審查 → 交叉驗證 → 評審彙總 → 結果通知 → 項目追蹤」的整條行政工作流交給智能體自動完成：

- **多渠道受理**：各計劃申請表、附件（PDF/DOCX/PPTX/XLSX/CSV/圖片/視頻）統一攝取解析
- **聲明式規則**：每個資助計劃的要求、格式、資格、評審準則用 YAML 配置，無需改代碼即可適配新計劃
- **完整性 & 合規審查**：必填資料、附件、格式、資格條件、潛在利益衝突逐項核查，缺失/錯誤/異常自動標記
- **跨源交叉驗證**：表單自報數據 vs 申請書內容自動比對，識別預算矛盾、信息不一致；批次級查重與預算離羣檢測
- **評審資料包**：自動生成標準化申請摘要、一頁式項目簡介、評審核對清單（Reviewer Pack）
- **評審彙總**：按維度權重計算加權總分，自動識別評分分歧並觸發複議建議，生成結果通知與後續待辦
- **項目追蹤**：里程碑、資助發放、定期報告跟蹤，逾期/臨期自動預警，輸出管理報告
- **全程可審計**：每個 Agent 動作寫入審計日誌，支持「人機協同」——關鍵節點顯式標記需人工複核

## 語言規範

本競賽面向香港，本倉庫**所有檔案一律使用繁體中文（香港慣用字）或英文**，不出現簡體中文：

- 文件、註解、提交訊息、示範數據、測試斷言均須遵守；
- 術語採用香港慣用寫法（人工智能、網絡、數據、軟件、視頻等）；
- 提交前執行 `python scripts/check_language.py`（CI 亦會自動執行），該腳本以 OpenCC `s2hk` 檢查全部受版本控制的文字檔，發現簡體字即失敗。

## 快速開始

```bash
pip install -r requirements.txt   # 最小依賴僅 pyyaml（其餘為可選解析庫）

# 跑通 demo 全流程（離線、無需任何 API Key）
python cli.py

# 啓動 Web 面板（http://127.0.0.1:8000）
python app.py

# 運行測試
python -m pytest tests -q
```

接入真實大模型（可選，顯著提升抽取與摘要質量）：

```bash
export OPENAI_API_KEY=sk-...        # 支持任意 OpenAI 兼容端點
export OPENAI_BASE_URL=https://...  # 可選
export OPENAI_MODEL=gpt-4o-mini     # 可選
python cli.py --llm openai
```

## 架構

```mermaid
flowchart LR
    A[多渠道提交資料] --> B[Ingest Agent<br/>多格式解析]
    B --> C[Extract Agent<br/>LLM 結構化抽取]
    C --> D[Check Agent<br/>完整性/格式/資格/利益衝突]
    D --> E[Verify Agent<br/>跨源交叉驗證/查重/異常檢測]
    E --> F[Summarize Agent<br/>摘要/一頁簡介/評審資料包]
    F --> G[Review Agent<br/>加權彙總/分歧識別/結果通知]
    G --> H[Track Agent<br/>里程碑/資助/預警/管理報告]
    I[(計劃規則 YAML<br/>聲明式配置)] --> D
    I --> G
    J[人工複核點] -.介入.-> D
    J -.介入.-> G
    K[Audit Trail<br/>全程審計日誌] --- B & C & D & E & F & G & H
```

## 目錄結構

```
├── configs/program_demo.yaml   # 示範計劃規則（聲明式配置）
├── data/demo/                  # 3 份示範申請（含異常用例）+ 評審評分
├── src/kkg/                    # 核心包
│   ├── ingest.py               # 多格式文檔攝取（可選依賴、優雅降級）
│   ├── extract.py              # LLM 抽取 + 交叉驗證 + 異常檢測
│   ├── check.py                # 完整性/格式/資格/利益衝突/查重
│   ├── summarize.py            # 摘要 / 一頁簡介 / 評審資料包
│   ├── review.py               # 加權彙總 / 分歧識別 / 通知 / 待辦
│   ├── track.py                # 里程碑 / 資助 / 預警 / 管理報告
│   ├── pipeline.py             # Agentic Workflow 編排 + 審計
│   ├── llm.py                  # 可插拔 LLM（Mock 離線可復現）
│   ├── program.py              # 計劃規則模型
│   └── models.py               # 數據模型 + 審計日誌
├── cli.py                      # 一鍵跑通全流程
├── app.py                      # FastAPI Web 面板
├── tests/                      # 端到端測試
└── docs/                       # 架構説明 / 計劃書大綱 / 評審映射
```

## Demo 數據説明（故意內置異常，驗證 Agent 檢出能力）

| 申請 | 預期檢出 |
|---|---|
| APP-2027-001 | ✅ 基本合規（僅建議項缺失提示） |
| APP-2027-002 | ⚠️ 表單預算 15 萬 vs 申請書 9.5 萬（跨源矛盾） |
| APP-2027-003 | ❌ 團隊 9 人超上限、預算 200 萬超計劃上限 50 萬、觸發評審人利益衝突（CUHK）、與 APP-2027-001 疑似重複、預算批次離羣 |

## 負責任 AI

- 所有自動化結論附 **severity + needs_human** 標記，關鍵決策（資格淘汰、利益衝突、複議）默認人工複核
- 審計日誌記錄每次 Agent 動作與依據，可追溯、可復現
- MockLLM 保證無外部調用時結果確定一致；真實 LLM 温度固定 0.2
- 數據僅在本機處理，不上傳第三方

## 路線圖

- [ ] 郵件/IMAP 渠道自動收件與附件歸檔
- [ ] 圖片 OCR 與視頻轉寫（接入多模態模型）
- [ ] 評審意見情感分析與評分校準建議
- [ ] 多計劃並行受理看板（Supabase 持久化）
- [ ] MCP 工具封裝，供其他 Agent 調用複用

---

License: MIT · Team: KK_Garden
