# GrantFlow · 團隊交接說明

> 更新日期：2026-11-20 ｜ **工作分支 `codex/web-workbench-merge` @ `b6e8d2f`**（已 rebase 到 `main` 最新）
> 倉庫：https://github.com/Hkate777/GrantFlow-AI-Agent-Challenge （舊名 KK_Garden，GitHub 自動重定向）
> 本地檢出：`C:\Users\25345\Documents\New project\KK_Garden`
> 本文件面向共同開發的隊友：專案現況、歷史線說明、如何接手、協作規範、風險與待辦。
> 相關文檔：[前次交接](<C:/Users/25345/Documents/New project/GrantFlow_交接文档.md>) · [全面審評與改進清單](<C:/Users/25345/Documents/New project/GrantFlow_全面审评与改进清单.md>) · [參賽手冊](<C:/Users/25345/Desktop/个人资料/《「智賦香江」AI智能體應用大賽 參賽手冊》.pdf>)

---

## 1. 專案與現況（一句話）

GrantFlow 對應「智賦香江」場景 ⑯「申請評審與項目管理流程自動化」：收件（網頁/`.eml`）→ 證據級抽取與跨源核對 → 規則校驗 → 評審彙總 → **人工簽發** → 資助後台帳；全程審計。

**目前真實狀態**（以本節為準）：

| 項目 | 現況 |
|---|---|
| 測試 | **83/83 通過**（`python -m pytest tests -q`） |
| 語言檢查 | 通過（OpenCC `s2hk` 警告模式；`data/demo` 舊簡體輸入已豁免） |
| Web 冒煙 | `/`、`app.js`、`styles.css`、`/api/program`、`/api/regions`、`/api/cases` 全 200 |
| 兩線整合 | **已完成**：Web 工作台線 + 多地區/隱私/導出線合併（見 §2） |
| 乾淨安裝 | 隔離 venv 只裝 `requirements.txt` → **83/83**（已補 `httpx`/`python-multipart`，CI 恢復綠燈） |
| 實測缺陷 | **4 個已修**（截止時間/評委人數/歷史包導出/列表分頁，見 §4） |
| 未合併 | 整合分支尚未併回 `main`（待隊友 PR 審查）；`main` 已打 `v1.0.0` tag |
| 人工驗收 | 完整瀏覽器流程、真實 OCR、機構資料、人工工時基線仍待驗收 |
| 第二輪審評 | P0-01/02/03/04/05/**11** 的代碼項已修（依賴鎖定、演示入口、統一評估口徑、簽發快照、自報日期移除、**台帳額度/未來日期/往返保真**），提交 `6f559ed`+`d0240fe`；P0-06 起的身份授權與 P0-10 剩餘項待隊友認領 |

---

## 2. ⚠️ 歷史線說明（接手必讀，最容易迷惑的部分）

這個倉庫經歷過**三條線**，接手前請理解拓撲：

```
origin/main (b88a8d3)  ← 主線：已被重寫過一次（d705aa7 初版起步，非 KK_ZHONG 舊歷史）
   │                     功能：多地区审查(16地区)、隐私去识别化、多模型、Excel导出、v1.0.0
   │
   ├── codex/complete-scenario-16 (1744ba3)  ← 已淘汰：基於舊 KK_ZHONG 歷史 bd1a4fb 的工作，
   │                                            其內容已全部以另一條路徑進入整合分支
   │
   └── codex/web-workbench-merge (b6e8d2f)  ← ★ 工作分支：main 最新 + Web 工作台全量移植
                                              + 4 個實測缺陷修復（rebase 為單一提交）
```

**要點**：
1. `origin/main` 曾被 force push 重寫（舊 `bd1a4fb` 歷史不再位於 main 上）——不要用舊 SHA 找代碼。
2. 我的工作（Web 工作台 + 缺陷修復）最初基於舊歷史（`1744ba3`），**已 rebase 到新 main 之上**（`b6e8d2f`）。
3. `1744ba3` 分支保留僅供歷史對照；**新工作一律從 `main` 或 `codex/web-workbench-merge` 開分支**。
4. 兩線融合的詳細決策記錄在提交 `b6e8d2f` 的 commit message。

---

## 3. 快速開始（新機器複現）

```powershell
Set-Location -LiteralPath 'C:/Users/25345/Documents/New project/KK_Garden'
git fetch origin; git switch codex/web-workbench-merge   # 或 git switch main 後開新分支

# 依賴：系統 Python 3.14（本次實測）或 CI 的 3.11；opencc 在 .venv（3.13）可用
python -m pip install -r requirements.txt
python -m pytest tests -q                     # 應 83/83
& '.venv/Scripts/python.exe' scripts/check_language.py   # 應通過（警告模式）

# Web 工作台（獨立臨時 DB）
$env:GRANTFLOW_DB = Join-Path ([IO.Path]::GetTempPath()) ('grantflow-' + [guid]::NewGuid() + '.sqlite3')
python app.py --port 8000                     # http://127.0.0.1:8000

# CLI（多地區 + 多模型）
python cli.py --llm mock --region HK --out <臨時目錄>
```

**環境變數**：`GRANTFLOW_DB`（SQLite 路徑）、`KKG_TESSERACT`/`KKG_OCR_LANG`（OCR）、
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`GEMINI_API_KEY`（**僅顯式 `--llm` 時生效**；`auto` 恆為本地 Mock）。

---

## 4. 本批交付（整合 + 4 個實測缺陷修復）

### 4.1 兩線整合（`b6e8d2f` 主體）

| 保留自 main 線 | 移植自工作台線 |
|---|---|
| 多地區審查引擎（16 個地區 YAML + `region.py`） | **完整 Web 工作台**：收件/補件/評分/複議/人工簽發/台帳/審計 |
| intake 與評審包的**隱私去識別化**（Redactor） | 存儲層（事務/版本衝突/提交冪等） |
| 多模型接入（OpenAI/Claude/Gemini） | `EvidenceSpan` 證據結構 + `parse_hkd` 金額解析 |
| Excel 導出、量化基準、v1.0.0 說明 | 12 個測試檔（含 AppTest 風格的 workspace API 測試） |

融合的關鍵檔案：`review.py`（對方分歧閾值 + 我方校驗/狀態機）、`pipeline.py`（對方 region+隱私 intake + 我方 `errors=` 與 assess）、`check.py`（對方 `check_region` + 我方細化校驗與 `application_ids` 查重）、`app.py`（完整工作台 + 對方地區演示端點）、`cli.py`（對方地區/多模型 + 我方導出修復）、`get_llm`（auto 恆本地）。

### 4.2 四個實測缺陷修復（各帶回歸測試，`tests/test_review_fixes.py`）

| # | 缺陷（審評實測證據） | 修復 | 回歸 |
|---|---|---|---|
| 1 | 截止時間依賴申請人自報 `submitted_at`，逾期申請可被受理 | 評估時以**服務端收件時間**（`received_at`，轉本地時區）對比計劃截止 → ERROR 發現 → `needs_compliance` 阻斷簽發 | `test_overdue_receipt_time_blocks_signoff` |
| 2 | 單一評審滿分即可簽發，無人數下限 | `Program.min_reviewers`（YAML 可配，默認 1）→ 不足產生 `insufficient_reviews` hold，簽發 409；補足後可簽發 | `test_min_reviewers_blocks_signoff` |
| 3 | 計劃配置移除後，歷史個案評審包導出 422 | 個案隨案保存**計劃快照**（`program_snapshot`）；配置移除後 `Program.from_snapshot` 重建導出 | `test_pack_export_survives_plan_removal` |
| 4 | 個案列表載入整批（1,000 宗 0.411s） | `CaseStore.list/count` 加 SQL `LIMIT/OFFSET`；`/api/cases?limit=&offset=` 返回 `{items,total,limit,offset}`；不帶參數保持舊陣列形狀（前端零破壞） | `test_case_list_pagination` |

---

## 5. 接手必讀：六個關鍵設計決策

1. **人工簽發原則**：`assess()` 的 `notice` 恆為 `None`，正式通知只在工作台簽發時生成（`result_notice`）；CLI/導出寫的是 `review_draft()` 評審進度草稿。任何改動不得讓系統自動出正式通知。
2. **assess 的 holds 順序即狀態語義**：`pending_deliberation → needs_materials → needs_compliance → invalid_scores → pending_scores → insufficient_reviews → needs_review`；`status = holds[0]`。調整順序會改變個案首選狀態與測試。
3. **`get_llm('auto')` 恆回本地 MockLLM**：外部模型須顯式指定 kind 且環境有密鑰。這是受控模型端點策略（審評 P2-04）——Web 預設不出境。
4. **計劃快照隨案**：`case["program_snapshot"]` 是歷史個案可查閱/匯出的依據；`Program.from_snapshot` 會重建嵌套 dataclass。改 `Program` 欄位時同步維護快照兼容。
5. **`min_reviewers` 兩個內置計劃均已設 2**：新建計劃 YAML 亦應設定（低於人數產生 `insufficient_reviews` hold，簽發被擋）。簽發時 decision 內保存 `program_snapshot` 鎖定規則版本——歷史包對應簽發時規則，避免「舊結果配新維度」。
6. **舊簡體輸入是語義夾具**：`data/demo/applications/app002`（簡體附件 vs 繁體表單 = 跨源矛盾發現的來源）與 `reviews_demo.json`（簡體 criteria 鍵兼容）**不得轉繁**；語言檢查的豁免清單/警告模式即為此服務。

---

## 6. 協作分工建議

| 模組 | 檔案 | 負責範圍 |
|---|---|---|
| 收件與工作流 | `src/kkg/workspace.py`、`submission.py`、`storage.py` | 個案生命週期、版本、冪等 |
| 校驗與抽取 | `src/kkg/check.py`、`extract.py`、`evidence.py`、`money.py` | 規則、證據、跨源核對 |
| 評審與追蹤 | `src/kkg/review.py`、`track.py` | 狀態機、加權、台帳 |
| 地區與隱私 | `src/kkg/region.py`、`privacy.py`、`configs/regions/*.yaml` | 多地區規則、去識別化 |
| 界面 | `app.py`、`web/*`、`cli.py` | API、前端、CLI |
| 導出與基準 | `src/kkg/export.py`、`scripts/benchmark.py`、`scripts/pitch_demo.py` | 匯出、量化、演示 |

**提交規範**：commit 用英文祈使句；**禁止提交** `.env`、`data/` 之外的本地 DB、任何真實機構資料；改 `Program`/`check.py`/`review.py` 必須附全套測試與語言檢查結果；`main` 已打 `v1.0.0` tag——正式版本變更走 PR + 新 tag。

**PR 檢查清單**：
- [ ] `python -m pytest tests -q` 83+/83+ 全綠
- [ ] `scripts/check_language.py`（或 `.venv` 版）通過/無新增警告
- [ ] CLI 煙測 + Web 冒煙各跑一次
- [ ] 涉及數字/行為變化 → 同步更新本文件 §1 與 RELEASE_NOTES

---

## 7. 已知風險與限制（如實）

1. **P0 人工項未動**（審評清單）：機構 OIDC/SSO、權限矩陣、環境分離、收件連接器、備份恢復演練、審計獨立保存——接真實資料前的硬性前置，需機構決策與人工執行。
2. **分頁的批次檢查僅限當前頁**：跨頁重複檢測需 P1 的持久化佇列/索引改造（`workspace.list` docstring 已注明）。
3. **舊個案無快照**：`program_snapshot` 自本版起記錄；升級前的歷史資料在配置移除後仍會 422（遷移腳本屬 P0-08）。
4. **`auto` 語義已收緊**：原先 auto 會自動用環境裡的外部模型密鑰，現改為恆本地。若有外部調用需求，請顯式 `--llm openai` 等。
5. **`.venv`（Python 3.13）缺 FastAPI/pytest**：完整測試用系統 Python 3.14；語言檢查用 `.venv`。
6. **時區近似**：截止比較用服務器本地時區的收件日期；正式機構部署應固定時區政策（審評 P0-05）。

---

## 8. 待辦（引用審評清單）

- [ ] **PR**：`codex/web-workbench-merge` → `main`，隊友審查後合併（main 已有 `v1.0.0` tag，合併後建議打 `v1.1.0`）
- [ ] P0-02/03：機構登入 + 個案級授權（代碼側最大塊，建議下一個迭代）
- [ ] P0-05：收件時間/回執正式化（本批已打地基：`received_at` 服務端記錄）
- [ ] P0-08：資料遷移腳本（舊個案補 `program_snapshot`）
- [ ] P1-05：評委最低人數在**簽發 UI**上的明確提示（後端已擋）
- [ ] P1-16：真實瀏覽器 E2E 納入 CI
- [ ] 全流程人工驗收（交接文檔步驟 4）：兩計劃 × 兩渠道、真實 OCR、工時基線

---

## 9. 檔案地圖

```
app.py                FastAPI 工作台（create_app 工廠 + 收件/評分/複議/簽發/台帳/導出 + 地區演示端點）
cli.py                CLI 六步全流程（多地區、多模型、導出修復、runs/ 隔離）
src/kkg/
  workspace.py        個案生命週期（評估/holds/快照/截止強制/分頁列表）
  storage.py          SQLite（事務/版本衝突/分頁/快照讀取）
  submission.py       安全收件（表單校驗/附件上限/.eml 解析）
  check.py            規則校驗（欄位/附件/資格/衝突 + check_region 地區審查）
  extract.py          證據級抽取 + 跨源核對（Evidence/EvidenceSpan）
  evidence.py/money.py  欄位標籤與 HKD 金額解析
  review.py           評審彙總 + 校驗 + assess 狀態機 + review_draft
  track.py            台帳（Disbursement/PeriodicReport/管理報告）
  pipeline.py         編排（region/privacy 參數、errors= 匯入）
  region.py/privacy.py  多地區審查 / 去識別化
  export.py           Excel 匯出
configs/program_demo.yaml     示範計劃（HK-ACC-2027）
configs/campus_pilot.yaml     校園試點計劃（第二套規則）
configs/regions/*.yaml        16 地區配置
web/                  前端（app.js/index.html/styles.css）
scripts/              check_language.py / benchmark.py / pitch_demo.py
tests/                83 項測試（test_workspace/test_cli/test_pipeline/test_review_fixes…）
docs/                 architecture.md / proposal-outline.md / RELEASE_NOTES_v1.0.0.md
```

---

## 10. 接手第一天建議

1. `git switch codex/web-workbench-merge && git pull` → `python -m pytest tests -q`（應 83/83）。
2. 起 Web 工作台（臨時 DB），載入示範申請，檢查 002 的「待複議」狀態與評審包導出。
3. 讀 [全面審評與改進清單](<C:/Users/25345/Documents/New project/GrantFlow_全面审评与改进清单.md>) 的 P0 表，認領一項（代碼側建議從 P0-02 登入授權開始）。
4. 開 PR 把 `codex/web-workbench-merge` 併回 `main`——這是目前最重要的一步：整合成果只在這個分支上。

> 本專案的核心原則：**證據可追溯、決定可重現、正式通知由人簽發**。改動時守住這三條。
