![GrantFlow Banner](assets/banner.svg)

# GrantFlow · 申請評審與項目管理流程自動化 Agent

> 「智賦香江」AI智能體應用大賽 · 指定賽道 · **場景16**（教育與文化創意賽道 · 校內行政工作流方向）
> 參賽隊伍：**KK_Garden**（2人）

GrantFlow面向大學、科技園、創投機構及創新創業支援單位，把「申請受理、資格審查、交叉驗證、評審彙總、人工簽發、結果通知、項目追蹤」的整條行政工作流交給智能體自動完成。系統有兩個入口：一鍵跑完全流程的命令行（CLI），以及可逐步操作個案生命週期的網頁工作台；兩者共用同一套規則引擎，而**正式結果通知只在工作台由人簽發，智能體永不自動發出**。
核心設計是**把每個資助計劃的差異做成配置、把敏感資訊擋在本機、把審查視角切換到當地**——一套引擎適配不同計劃、不同地區、不同模型，而無需改動程式：

- **多渠道受理**：網頁表單、`.eml` 郵件附件及資料夾三種渠道；附件（PDF/DOCX/PPTX/XLSX/CSV/圖片/視頻）統一攝取解析
- **聲明式規則**：每個資助計劃的要求、格式、資格、評審準則用YAML配置，無需改代碼即可適配新計劃
- **完整性 & 合規審查**：必填資料、附件、格式、資格條件、潛在利益衝突逐項核查，缺失/錯誤/異常自動標記
- **跨源交叉驗證**：表單自報數據vs申請書內容自動比對，識別預算矛盾、信息不一致；抽取欄位附原文位置佐證（出處行號與引文）；批次級查重與預算離羣檢測
- **評審資料包**：自動生成標準化申請摘要、一頁式項目簡介、評審核對清單（Reviewer Pack）
- **評審彙總**：按維度權重計算加權總分，自動識別評分分歧並觸發複議建議，生成評審進度草稿與後續待辦
- **人工簽發工作台**：個案狀態機自動推導「待補件、資格待核、待評分、評分待更正、待複議、評審人數不足、待人工覆核、待簽發」；補件、評分、複議記錄、簽發各有版本號防衝突；截止時間以服務端收件時間為準，評委人數不足或規則快照不符會擋住簽發
- **帳號與權限**：本機帳號、HTTP Basic 登入、scrypt 加鹽口令；停用帳號即時撤銷全部工作階段；工作台操作綁定服務端操作員身份
- **項目追蹤**：里程碑、資助發放、定期報告跟蹤，撥款合計不得超出核准額度，逾期/臨期自動預警，輸出管理報告
- **全程可審計**：每個Agent動作寫入審計日誌，關鍵節點標記為需人工複核
- **多地區審查**：內置15個地區（中國香港、中國台灣、中國內地、新加坡、美國、英國、澳洲、加拿大、日本、韓國、阿聯酋、沙特、馬來西亞、泰國、埃及），Agent按地區載入語言字形、當地法規、貨幣格式及評審慣例；法規依據真實法律（UK GDPR、Privacy Act 1988、PIPEDA、PDPL、Law 151/2020 等）
- **私隱與商業敏感防護**：送模型前先去識別化，電郵、電話、身份證、銀行卡、合約編號等以不可逆佔位符取代，原始值不離開本機；對外評審包、Excel總表與審計日誌均無明文敏感資訊
- **多模型可插拔**：Mock離線可復現，亦支持 OpenAI 兼容端點、Anthropic Claude、Google Gemini；`--llm auto` 按已配置的API Key自動選擇，無密鑰時優雅降級

## 語言規範

項目展示以香港繁體及英文為主。README、docs、註解、提交訊息、示範數據及測試斷言一律使用繁體中文（香港慣用字）或英文，不出現簡體中文。`configs/regions/` 內的地區配置除外，那些是Agent用來處理多語言申請的資料：

- 文件、註解、提交訊息、示範數據、測試斷言均須遵守；
- **繁體必須符合香港慣用術語或香港書面語，不得使用台灣地區慣用語、內地書面語或直接以簡體逐字轉繁的產物**；有疑問時以香港政府、香港教育局及本地傳媒的用字習慣為準；
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

# 啓動 Web 工作台（http://127.0.0.1:8000）
python app.py

# 運行測試
python -m pytest tests -q
```

Web 工作台首次啓動會建立兩個本機示範帳號：`admin`（管理員）及 `demo`（操作員）。初始口令由環境變數 `GRANTFLOW_BOOTSTRAP_PASSWORD` 指定；未設定時在啓動日誌查看一次性提示。工作台支援收件、補件、評分、複議記錄、人工簽發、資助台帳與審計查閱；命令行則適合批次處理與演示復現。數據庫路徑由 `GRANTFLOW_DB` 指定，默認存於 `output/`。

接入真實大模型（可選，可改善抽取與摘要品質；不配置則用離線Mock）：

```bash
# OpenAI兼容端點（含DeepSeek、通義、Moonshot等）
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://...  # 可選
export OPENAI_MODEL=gpt-4o-mini     # 可選
python cli.py --llm openai

# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...
python cli.py --llm anthropic

# Google Gemini
export GEMINI_API_KEY=...
python cli.py --llm gemini

# 自動：按已配置的API Key依次選擇，全部沒有則降級Mock
python cli.py --llm auto
```

私隱防護默認開啟。如需在本機受信任環境關閉（不建議用於真實申請）：

```bash
# 在程式中 Pipeline(prog, privacy=False)；CLI/Web 默認開啟
```

按地區審查（Agent會載入當地語言字形、法規、格式及評審慣例）：

```bash
python cli.py --region HK    # 中國香港（繁體／英文，個人資料私隱條例）
python cli.py --region JP    # 日本（APPI／個人情報保護法）
python cli.py --region KR    # 韓國（PIPA／個人情報保護法）
python cli.py --region GB    # 英國（英文，UK GDPR／Data Protection Act）
python cli.py --region AU    # 澳洲（英文，Privacy Act 1988／APP）
python cli.py --region AE    # 阿聯酋（PDPL 2021年第45號聯邦法令）
python cli.py --region SA    # 沙特（PDPL／SDAIA）
python cli.py --region EG    # 埃及（Law No.151/2020）
python cli.py --region CN    # 中國內地（簡體，個人信息保護法／數據安全法）
```

共15個地區，完整清單見 `configs/regions/`。Web面板亦有地區下拉選單；新增地區只需加入一份yaml。選地依據及法規對照見 `docs/architecture.md`。

## 架構

```mermaid
flowchart LR
    ENTRY1[命令行 CLI<br/>批次全流程] --> B
    ENTRY2[Web 工作台<br/>逐步操作個案] --> B
    B[Ingest Agent<br/>多格式解析] --> C[Extract Agent<br/>LLM 結構化抽取<br/>原文位置佐證]
    C --> D[Check Agent<br/>完整性/格式/資格/利益衝突]
    D --> E[Verify Agent<br/>跨源交叉驗證/查重/異常檢測]
    E --> F[Summarize Agent<br/>摘要/一頁簡介/評審資料包]
    F --> G[Review Agent<br/>加權彙總/分歧識別/狀態機]
    G --> SIGN{人工簽發<br/>工作台}
    SIGN -->|通過或通過| H[Track Agent<br/>里程碑/資助/預警/管理報告]
    SIGN -->|正式結果通知| N[結果通知<br/>僅人簽發時生成]
    I[(計劃規則 YAML<br/>聲明式配置)] --> D
    I --> G
    J[人工複核點<br/>補件/評分/複議] -.介入.-> G
    K[Audit Trail<br/>全程審計日誌] --- B & C & D & E & F & G & SIGN & H
```

## 目錄結構

```
├── assets/
│   └── banner.svg              # 倉庫封面橫幅
├── configs/
│   ├── program_demo.yaml       # 示範計劃規則（聲明式配置）
│   ├── campus_pilot.yaml       # 校園試點計劃（第二套規則）
│   └── regions/                # 15個地區資料（HK/TW/CN/SG/US/GB/AU/JP/KR/CA/AE/SA/MY/TH/EG）
├── data/demo/                  # 3 份示範申請（含異常用例）+ 評審評分
├── src/kkg/                    # 核心包
│   ├── ingest.py               # 多格式文檔攝取（可選依賴、優雅降級）
│   ├── extract.py              # LLM 抽取 + 交叉驗證 + 異常檢測
│   ├── evidence.py             # 欄位原文位置佐證（出處行號/引文）+ HKD 金額解析
│   ├── money.py                # 港幣金額嚴格解析與格式化
│   ├── check.py                # 完整性/格式/資格/利益衝突/查重 + 地區審查
│   ├── summarize.py            # 摘要 / 一頁簡介 / 評審資料包
│   ├── review.py               # 加權彙總 / 分歧識別 / assess 狀態機 / 評審進度草稿
│   ├── track.py                # 里程碑 / 資助 / 預警 / 管理報告 / 台帳校驗
│   ├── workspace.py            # 個案生命週期：收件/補件/評分/複議/人工簽發/快照
│   ├── storage.py              # SQLite 儲存（串行化寫入/版本衝突/分頁）
│   ├── submission.py           # 安全收件（表單校驗/附件上限/.eml 解析/指紋）
│   ├── auth.py                 # 本機帳號 / HTTP Basic / scrypt 口令 / 停用即撤銷
│   ├── pipeline.py             # Agentic Workflow 編排 + 審計
│   ├── llm.py                  # 可插拔模型（Mock/OpenAI/Claude/Gemini，優雅降級）
│   ├── privacy.py              # 私隱與商業敏感資訊去識別化 + 審計
│   ├── program.py              # 計劃規則模型（含快照重建）
│   ├── models.py               # 數據模型 + 審計日誌
│   ├── region.py               # 地區模型 + 字形檢測
│   └── export.py               # 評審彙總 Excel 匯出（三個工作表，敏感欄位脫敏）
├── cli.py                      # 命令行一鍵跑通全流程
├── app.py                      # FastAPI Web 工作台（create_app 工廠 + 近20個 API）
├── web/                        # 工作台前端（index.html/app.js/styles.css）
├── scripts/
│   ├── check_language.py       # 語言規範檢查（預設警告，--strict 嚴格）
│   ├── benchmark.py            # 量化基準測試：計劃書「可量化成效」
│   ├── export_summary.py       # 匯出評審總覽 Excel
│   └── pitch_demo.py           # 一鍵生成決賽演示全套物料：output/pitch/
├── tests/                      # 96 項端到端測試
├── TEAM_HANDOFF.md             # 團隊交接説明（現況/歷史線/設計決策/分工）
└── docs/                       # 架構説明 / 計劃書大綱 / 版本説明 / 評審映射
```

## 演示與量化數據

```bash
python scripts/benchmark.py     # 輸出 output/benchmark_report.md（效率與檢出率）
python scripts/export_summary.py  # 輸出 output/review_summary.xlsx（申請總覽/發現明細/評分明細）
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
- **私隱優先**：送模型前先去識別化，原始敏感值不離開本機；映射只在記憶體，不落盤；對外評審包、Excel與審計日誌均無明文敏感資訊
- 地區合規檢查為建議性提示，不自動判定資格，最終由人工確認適用性
- MockLLM保證無外部調用時結果確定一致；真實模型 temperature 固定0.2
- 接入真實模型時數據已先脫敏，降低敏感資訊外洩至第三方的風險

## 路線圖

已實現（本版）：

- [x] 多渠道收件：網頁表單、`.eml` 郵件附件及資料夾（`/api/mail` + `submission.read_message`）
- [x] 多計劃並行受理工作台：SQLite 持久化、個案版本與快照（`workspace.py` + `storage.py`）
- [x] 本機帳號與操作員身份、人工簽發與資助台帳

後續迭代：

- [ ] IMAP 伺服器定時拉取與附件自動歸檔（現支援 `.eml` 手動匯入）
- [ ] 圖片OCR與視頻轉寫（接入多模態模型）
- [ ] 評審意見情感分析與評分校準建議
- [ ] 機構 OIDC/SSO 登入與個案級權限矩陣（現為本機帳號）
- [ ] MCP工具封裝，供其他Agent調用複用

---

License: MIT · Team: KK_Garden
