# GrantFlow · 申請評審與項目管理流程自動化 Agent

> 「智賦香江」AI智能體應用大賽 · 指定賽道 · **場景16**（教育與文化創意賽道 · 校內行政工作流方向）
> 參賽隊伍：**KK_Garden**（2人）

GrantFlow面向大學、科技園、創投機構及創新創業支援單位，把「申請受理：資格審查：交叉驗證：評審彙總：結果通知：項目追蹤」的整條行政工作流交給智能體自動完成：

- **多渠道受理**：各計劃申請表、附件（PDF/DOCX/PPTX/XLSX/CSV/圖片/視頻）統一攝取解析
- **聲明式規則**：每個資助計劃的要求、格式、資格、評審準則用YAML配置，無需改代碼即可適配新計劃
- **完整性 & 合規審查**：必填資料、附件、格式、資格條件、潛在利益衝突逐項核查，缺失/錯誤/異常自動標記
- **跨源交叉驗證**：表單自報數據vs申請書內容自動比對，識別預算矛盾、信息不一致；批次級查重與預算離羣檢測
- **評審資料包**：自動生成標準化申請摘要、一頁式項目簡介、評審核對清單（Reviewer Pack）
- **評審彙總**：按維度權重計算加權總分，自動識別評分分歧並觸發複議建議，生成結果通知與後續待辦
- **項目追蹤**：里程碑、資助發放、定期報告跟蹤，逾期/臨期自動預警，輸出管理報告
- **全程可審計**：每個Agent動作寫入審計日誌，關鍵節點標記為需人工複核
- **多地區審查**：可切換香港、台灣、中國內地、新加坡及美國，Agent按地區載入語言、法規、格式及評審慣例

## 語言規範

項目展示以香港繁體及英文為主。README、docs、註解、提交訊息、示範數據及測試斷言一律使用繁體中文（香港慣用字）或英文，不出現簡體中文。`configs/regions/` 內的地區配置除外，那些是Agent用來處理多語言申請的資料：

- 文件、註解、提交訊息、示範數據、測試斷言均須遵守；
- **繁體必須符合香港慣用術語或香港書面語，不得使用台灣慣用語、大陸書面語或直接以簡體逐字轉繁的產物**；有疑問時以香港政府、香港教育局及本地傳媒的用字習慣為準；
- 香港標準詞彙對照（左側禁用，右側為推薦寫法）：

  | 禁用（非香港標準） | 香港慣用寫法 |
  |---|---|
  | 軟體 | 軟件 |
  | 網路 | 網絡 |
  | 視訊 | 視頻 |
  | 行動電話 | 手提電話 |
  | 智慧財產 | 知識產權 |
  | 人工智慧 | 人工智能 |
  | 質量（指素質時） | 質素 |

  常見香港書面用字：人工智能、網絡、數據、軟件、視頻、資訊、項目、質素、註冊等；「信息」與「資訊」均可，但同一文件內須保持一致；
- 提交前可執行 `python scripts/check_language.py`（CI亦會自動執行）：腳本以OpenCC `s2hk` 檢查全部受版本控制的文字檔。**CI採用警告模式**，只提示、不阻塞流程（OpenCC偶爾會把香港標準「說明」誤判為「説明」）；如需嚴格把關，可自行執行 `python scripts/check_language.py --strict`。

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

接入真實大模型（可選，可改善抽取與摘要品質）：

```bash
export OPENAI_API_KEY=sk-...        # 支持任意 OpenAI 兼容端點
export OPENAI_BASE_URL=https://...  # 可選
export OPENAI_MODEL=gpt-4o-mini     # 可選
python cli.py --llm openai
```

按地區審查（Agent會載入當地語言、法規、格式及評審慣例）：

```bash
python cli.py --region HK    # 香港（繁體／英文，個人資料私隱條例）
python cli.py --region CN    # 中國內地（簡體，個人信息保護法）
python cli.py --region US    # 美國（英文，privacy/data protection）
python cli.py --region TW    # 台灣
python cli.py --region SG    # 新加坡（英文，PDPA）
```

Web面板亦有地區下拉選單。地區配置見 `configs/regions/`，新增地區只需加入一份yaml。

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
├── configs/
│   ├── program_demo.yaml       # 示範計劃規則（聲明式配置）
│   └── regions/                # HK/TW/CN/SG/US 地區資料
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
│   ├── models.py               # 數據模型 + 審計日誌
│   ├── region.py               # 地區模型 + 字形檢測
│   └── export.py               # 評審彙總 Excel 匯出（三個工作表）
├── cli.py                      # 一鍵跑通全流程
├── app.py                      # FastAPI Web 面板
├── scripts/
│   ├── check_language.py       # 語言規範檢查（預設警告，--strict 嚴格）
│   ├── benchmark.py            # 量化基準測試：計劃書「可量化成效」
│   └── pitch_demo.py           # 一鍵生成決賽演示全套物料：output/pitch/
├── tests/                      # 端到端測試
└── docs/                       # 架構説明 / 計劃書大綱 / 評審映射
```

## 演示與量化數據

```bash
python scripts/benchmark.py     # 輸出 output/benchmark_report.md（效率與檢出率）
python -m kkg.export            # 輸出 output/review_summary.xlsx（申請總覽/發現明細/評分明細）
python scripts/pitch_demo.py    # 一鍵生成全套決賽物料（含 8 分鐘演示腳本）
```

`pitch_demo.py` 會把評審資料包、結果通知、待辦、管理報告、彙總Excel、基準報告及審計日誌集中輸出到 `output/pitch/`，適合決賽現場直接展示。

## Demo數據説明（故意內置異常，驗證Agent檢出能力）

| 申請 | 預期檢出 |
|---|---|
| APP-2027-001 | [通過] 基本合規（僅建議項缺失提示） |
| APP-2027-002 | [注意] 表單預算 15 萬 vs 申請書 9.5 萬（跨源矛盾） |
| APP-2027-003 | [未通過] 團隊 9 人超上限、預算 200 萬超計劃上限 50 萬、觸發評審人利益衝突（CUHK）、與 APP-2027-001 疑似重複、預算批次離羣 |

## 負責任AI

- 所有自動化結論附 **severity + needs_human** 標記，關鍵決策（資格淘汰、利益衝突、複議）默認人工複核
- 審計日誌記錄每次Agent動作與依據，可追溯、可復現
- MockLLM保證無外部調用時結果確定一致；真實LLM温度固定0.2
- 數據僅在本機處理，不上傳第三方

## 路線圖

- [ ] 郵件/IMAP渠道自動收件與附件歸檔
- [ ] 圖片OCR與視頻轉寫（接入多模態模型）
- [ ] 評審意見情感分析與評分校準建議
- [ ] 多計劃並行受理看板（Supabase持久化）
- [ ] MCP工具封裝，供其他Agent調用複用

---

License: MIT · Team: KK_Garden
