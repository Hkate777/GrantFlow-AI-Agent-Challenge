# GrantFlow · 團隊交接說明

> 更新日期：2026-11-21 ｜ **主線 `main`**（所有整合、安全、字形、角色矩陣、可追溯模組、Web 可追溯分頁與語義注入偵測均已併入；本地與遠端一致、CI 全綠；確切 HEAD 以 `git rev-parse HEAD` 及遠端 `main` 分支為準，不在本文件寫死 SHA 以免自我過期）
> 倉庫：https://github.com/Hkate777/GrantFlow-AI-Agent-Challenge （舊名 KK_Garden，GitHub 自動重定向）
> 本地檢出：`C:\Users\25345\Documents\New project\KK_Garden`
> 本文件面向共同開發的隊友：專案現況、歷史線說明、如何接手、協作規範、風險與待辦。
> 相關文檔：[前次交接](<C:/Users/25345/Documents/New project/GrantFlow_交接文档.md>) · [全面審評與改進清單](<C:/Users/25345/Documents/New project/GrantFlow_全面审评与改进清单.md>) · [參賽手冊](<C:/Users/25345/Desktop/个人资料/《「智賦香江」AI智能體應用大賽 參賽手冊》.pdf>)

> **最新進度（2026-11-21）**：`main` 已由上而下串接——字形標準化引擎、安全護欄與加密密鑰環、權限角色矩陣 P0-07（隊友提交）、追蹤去 emoji 與日韓字形糾正、地區時區支援（當地今天判定）。提交歷史已重寫以 corrected 作者身份（`KK_ZHONG <hellokate757@gmail.com>`），並已加入五項可追溯與質量閉環模組、把它們接入 Web 工作台「可追溯」分頁與語義注入偵測。版本號維持 **v1.1.0**（v1.2.0 已移除，安全/角色/時區改動不單獨發版，依約不急於升版）。
>
> **最新進度（2026-11-22，P1-04 評審獨立性工作流）**：個案級**評審指派**（經辦）→ **利益衝突申報**（有衝突即迴避並移除其評分，審計留痕）→ **身份綁定的獨立評分**（`POST /scores` 服務端強制評委名=登入賬號、按 `reviewer_id` upsert）→ **盲評投影**（評審簽發前只見自己的評分與狀態，簽發後解除）→ 新狀態 **`pending_assignment`（待指派評審）**；批量評分 PUT 保留為本機演示/管理員更正通道（遠端模式本就強制綁定）。新增演示賬號 `reviewer2_demo`（評審乙，僅本機演示模式，舊安裝冪等補建）。前端按角色顯隱（我的評審任務/指派面板/申報表單），服務端始終為硬閘。移植說明：本輪在隊友重寫後的 `main` 之上落實，沿用其 `reviewer_id` 命名與遠端/演示雙模式設計。

---

## 1. 專案與現況（一句話）

GrantFlow 對應「智賦香江」場景 ⑯「申請評審與項目管理流程自動化」：收件（網頁/`.eml`）→ 證據級抽取與跨源核對 → 規則校驗 → 評審彙總 → **人工簽發** → 資助後台帳；全程審計。

**目前真實狀態**（以本節為準）：

| 項目 | 現況 |
|---|---|
| 測試 | **204 passed + 8 skipped**（本機缺 opencc 部分；`python -m pytest tests -q`；唯一失敗為 vault 檔案權限位的 Windows 環境差異，與改動無關。含 P1-04 新增 `tests/test_assignments.py` 7 項：指派/申報/獨立評分/盲評/迴避替補閉環） |
| 語言檢查 | 通過（OpenCC `s2hk` 警告模式；`data/demo` 舊簡體輸入已豁免） |
| Web 冒煙 | `/`、`app.js`、`styles.css`、`/api/program`、`/api/regions`、`/api/cases` 全 200 |
| 兩線整合 | **已完成**：Web 工作台線 + 多地區/隱私/導出線合併（見 §2） |
| 乾淨安裝 | 隔離 venv 只裝 `requirements.txt` → 全綠（依賴已鎖定相容區間並補 `httpx`/`httpx2`/`python-multipart`，CI 綠燈） |
| 身份與角色 | **P0-06/07 已落地**：所有 /api 需 HTTP Basic；角色矩陣 operator/reviewer/approver/finance/admin（**經辦另含評審指派**）；Basic 短時快取降低 scrypt 成本、停用即清空；遠端模式不建立/自動停用弱口令演示賬號；評分身份綁定登入帳號（`reviewer_id`），衝突以結構化 ID 比對；`decline_allowed` 已修復「缺材料不能否決」；前端移除寫死 demo 提示 |
| 評審工作流 | **P1-04 已落地**：個案級評審指派（`POST /assignments`，operator，經 auth 校驗 reviewer 角色）→ 利益申報（`POST /conflict`；申報有衝突即迴避、移除其評分、須經辦替補）→ 獨立評分（`POST /scores`，身份綁定 upsert，未指派/未申報/已迴避一律拒收）→ 盲評投影（`_blind`：reviewer 角色簽發前只見本人評分與 status/holds/todos，列表隱總分、評審包 review.json 同樣盲評，簽發後解除）；狀態機新增 `pending_assignment`（插在待評分之前、材料/資格/複議之後，並列入程序性 holds 不可否決）；可指派名單 `GET /api/reviewers` |
| 安全與私隱 | **已加固**：注入偵測前移至送模型之前（規則＋啟發式，**僅提示不保證攔截**）、隔離包裝、輸出審查；`output_gate.py` 讓 `ensure_safe_output()` 真正接入 Web/CLI 評審包、Excel 與通知；去識別化不再 `restore()` 回明文，正則加 Luhn/語境降低誤傷；密鑰環明文不進 `os.environ`；端點 `/api/security/*`；CLI `scripts/vault_cli.py keys` |
| 字形標準化 | **15 地區**配置；OpenCC `s2hk`/`s2twp`/`t2s` 逐字精確標準化 + 香港標準字白名單；`detect_script` 支援 hant/hans/ja/ko/latin；端點 `/api/script/audit`、`/api/script/regions` |
| 實測缺陷 | **4 個已修**（截止時間/評委人數/歷史包導出/列表分頁，見 §4） |
| 合併狀態 | **全部併入 `main`**；本地與遠端 tree 一致、工作區乾淨、CI 全綠（確切 SHA 見 `git rev-parse HEAD`）；`v1.0.0`/`v1.1.0` tag 保留，v1.2.0 已移除，版本號現為 v1.1.0 |
| 人工驗收 | 完整瀏覽器流程、真實 OCR、機構資料、人工工時基線仍待驗收 |
| 審評整改 | P0-01～07、11 的代碼項均已修（依賴鎖定、演示入口、統一評估口徑、簽發快照、自報日期移除、台帳額度/未來日期/往返保真、本地賬號+HTTP Basic+服務端操作者+停用即時生效、**角色授權矩陣 P0-07**）；剩餘 P0 多為機構人工項（OIDC/SSO、環境分離、備份演練、審計獨立保存） |

---

## 2. 歷史線說明（接手必讀，最容易迷惑的部分）

這個倉庫早期經歷過**三條線**，但**已全部收斂到單一 `main`**。接手前請理解拓撲：

```
main  ← [主線] 唯一主線：本地與遠端一致、CI 全綠（HEAD 見 git rev-parse）
   │              近期自上而下：字形 → 安全 → 角色矩陣（隊友）→ 去emoji/日韓字形 → 地區時區
   │              tag：v1.0.0 / v1.1.0（v1.2.0 已移除）
   │
   ├── codex/complete-scenario-16 (1744ba3)  ← 已淘汰、僅供歷史對照
   │
   └── codex/web-workbench-merge (9f6de78)   ← 已淘汰：其 Web 工作台與缺陷修復內容
                                                已全部併入 main，勿再從此分支開新工作
```

**要點**：
1. `origin/main` 早期曾被 force push 重寫（舊 `bd1a4fb` 歷史不再位於 main 上）——不要用舊 SHA 找代碼。
2. 兩條 `codex/*` 分支的內容**均已併入 main**，現只剩歷史對照價值。
3. **新工作一律從 `main` 開分支**；PR 目標為 `main`。
4. 兩線融合的詳細決策記錄在早期提交 `b6e8d2f` / `9f6de78` 的 commit message。
5. 2026-11-20 曾發生一次誤推送短暫覆蓋隊友的角色矩陣提交；已即時發現並以隊友提交為 parent 重建合併提交（`280a60f`）修復，現 `test_roles.py` 與 `app.py`/`auth.py` 的角色守衛完整保留、CI 全綠。教訓：推送前先 `git fetch` 確認遠端 SHA。

---

## 3. 快速開始（新機器複現）

```powershell
Set-Location -LiteralPath 'C:/Users/25345/Documents/New project/KK_Garden'
git fetch origin; git switch main             # 新工作從 main 開分支

# 依賴：Python 3.11（CI 與本次實測）；opencc 為可選（語言檢查用）
python -m pip install -r requirements.txt
python -m pytest tests -q                     # 應 204 passed + 8 skipped（缺 opencc）；vault 權限位 1 項 Windows 環境差異
python scripts/check_language.py              # 應通過（警告模式）

# Web 工作台（獨立臨時 DB）
$env:GRANTFLOW_DB = Join-Path ([IO.Path]::GetTempPath()) ('grantflow-' + [guid]::NewGuid() + '.sqlite3')
python app.py --port 8010                     # http://127.0.0.1:8010（8000 常被其他應用佔用）
                                              # 首次啟動自動建 admin/demo 及角色演示賬號（含 P1-04 的 reviewer2_demo）；
                                              # 瀏覽器彈出登入框，經辦用 demo/demo，評審流程用 reviewer_demo / reviewer2_demo

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
2. **assess 的 holds 順序即狀態語義**：`pending_deliberation → needs_materials → needs_compliance → invalid_scores → pending_assignment → pending_scores → insufficient_reviews → needs_review`；`status = holds[0]`。調整順序會改變個案首選狀態與測試（P1-04 把「待指派評審」插在「待評分」之前——指派是評分的前置條件，但排在材料/資格/複議之後；`pending_assignment` 亦列入程序性 holds，不可否決）。
3. **`get_llm('auto')` 恆回本地 MockLLM**：外部模型須顯式指定 kind 且環境有密鑰。這是受控模型端點策略（審評 P2-04）——Web 預設不出境。
4. **計劃快照隨案**：`case["program_snapshot"]` 是歷史個案可查閱/匯出的依據；`Program.from_snapshot` 會重建嵌套 dataclass。改 `Program` 欄位時同步維護快照兼容。
5. **`min_reviewers` 兩個內置計劃均已設 2**：新建計劃 YAML 亦應設定（低於人數產生 `insufficient_reviews` hold，簽發被擋）。簽發時 decision 內保存 `program_snapshot` 鎖定規則版本——歷史包對應簽發時規則，避免「舊結果配新維度」。
6. **舊簡體輸入是語義夾具**：`data/demo/applications/app002`（簡體附件 vs 繁體表單 = 跨源矛盾發現的來源）與 `reviews_demo.json`（簡體 criteria 鍵兼容）**不得轉繁**；語言檢查的豁免清單/警告模式即為此服務。
7. **評審獨立性三閘（P1-04）**：① 評審只可經 `POST /scores` 提交**本人**那一行（服務端綁定 `reviewer_id`，未指派/未申報/已迴避一律 422）；② 申報利益衝突即迴避並**移除其已有評分**（審計留痕），活躍指派不足 `min_reviewers` 時個案回到待指派、須經辦替補；③ **盲評投影** `_blind()`：reviewer 角色在簽發前不見他人評分/加權總分/分歧，簽發後解除。批量 `PUT /scores` 保留為本機演示（遠端模式本就綁定身份）與 admin 更正通道。改評分相關代碼時，這三閘與 `tests/test_assignments.py` 必須同步守住。

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

**提交規範**：commit 用繁體中文補英文前綴（`feat:`/`fix:`/`docs:`）；**禁止提交** `.env`、任何本地 DB（`output/` 已被忽略）、任何真實機構資料；改 `Program`/`check.py`/`review.py` 必須附全套測試與語言檢查結果。**推送前必先 `git fetch origin` 確認遠端 SHA**（見 §2 第 5 點教訓）；git 的 GnuTLS 後推送不穩時改用 GitHub Git Data API。`main` 已打 `v1.0.0`/`v1.1.0` tag——正式版本變更走 PR + 新 tag，不急於升版。

**PR 檢查清單**：
- [ ] `python -m pytest tests -q` 141/141 全綠
- [ ] `scripts/check_language.py` 通過/無新增警告
- [ ] CLI 煙測 + Web 冒煙各跑一次
- [ ] `git fetch origin` 確認遠端無新提交後再 push
- [ ] 涉及數字/行為變化 → 同步更新本文件 §1 與 RELEASE_NOTES

---

## 7. 已知風險與限制（如實）

1. **P0 人工項未動**（審評清單）：機構 OIDC/SSO、環境分離、收件連接器、備份恢復演練、審計獨立保存——接真實資料前的硬性前置，需機構決策與人工執行。（代碼側的 HTTP Basic 身份與角色矩陣 P0-06/07 已落地，但不替代機構級 OIDC/SSO。）
2. **分頁的批次檢查僅限當前頁**：跨頁重複檢測需 P1 的持久化佇列/索引改造（`workspace.list` docstring 已注明）。
3. **舊個案無快照**：`program_snapshot` 自本版起記錄；升級前的歷史資料在配置移除後仍會 422（遷移腳本屬 P0-08）。
4. **`auto` 語義已收緊**：原先 auto 會自動用環境裡的外部模型密鑰，現改為恆本地。若有外部調用需求，請顯式 `--llm openai` 等。
5. **`.venv`（Python 3.13）缺 FastAPI/pytest**：完整測試用系統 Python 3.14；語言檢查用 `.venv`。
6. **時區近似**：截止比較用服務器本地時區的收件日期；正式機構部署應固定時區政策（審評 P0-05）。

---

## 7a. 部署與網絡防護（最新疊代）

`src/kkg/serve.py` 集中管理部署配置：本機預設只聽 loopback；設 `GRANTFLOW_HOST=0.0.0.0` 才接受遠端。`app.py` 的中間件加入每地址滑動窗口限流、連續認證失敗臨時封鎖、Host/Origin/CIDR 約束、請求大小上限與安全響應頭；`output/security.log` 只記事件、地址、路徑與狀態碼，不含憑據。健康端點 `/healthz`、`/readyz` 供容器與負載均衡器使用。`scripts/start.sh` 提供 local/remote/docker/test 四種模式；Fly.io 與 Render 範例在 `deploy/`。反向代理必須設 `GRANTFLOW_TRUST_PROXY=true`，並把 Host、X-Forwarded-* 正確轉發；直接對外則保持 `false`。詳情見 `docs/deployment.md`。

**外部審查回饋整改（P1）**：`output_gate.py` 已把「輸出閘門」從文件變成實際呼叫點（Web/CLI 包、Excel、通知）；注入掃描移到模型呼叫之前，並明確標示「規則＋啟發式、不保證攔截」；`ReviewScore.reviewer_id` 與 `Finding.subject_id` 落實身份綁定與結構化衝突比對；`pipeline.intake()` 不再 `restore()` 明文；私隱正則加入 Luhn 與身份/護照語境；Basic 憑據短時快取但仍即時撤銷；密鑰環不再注入環境變數；遠端模式停用演示弱口令；`decline_allowed` 修復「缺材料不能否決」；基準報告標示 Mock 口徑限制。回歸測試見 `tests/test_audit_fixes.py`。

**可追溯與質量閉環（本次新增）**：`guard.build_ledger/check_ledger` 以可信數字台賬攔截通知中的金額/分數幻覺（年份與≤30序數豁免，接入 `output_gate.gate_text` 與簽發通知）；`explain.evidence_for` 由 Finding 規則生成依據卡（原始出處/排序/理由，零 LLM、零新數字，缺失走 imputed）；`scoring.validate_weights` strict 要求和為 1.0、relative 相容 3/3/2/2，`deterministic_score` 缺維度不補零；`eval_traces` 三項零成本規則判據 + 可選 Judge（失敗不影響規則）；`compliance.classify` refuse/degrade/note 三級 + 法規依據 + 「未命中不代表合規」聲明；`decision_journal` 記錄 LLM 判定並按模型拆分回看準確率。`Pipeline.traceability()` 供 Web/CLI 共用，CLI `[7/7]` 寫出 `output/traceability/`。回歸測試見 `tests/test_traceability.py`（23 項）。

**仍未完成（對接真實資料前硬門檻）**：機構級 OIDC/SSO、環境分離、備份演練、獨立審計保存、真實模型性能基線與 WAF/平台防護。

---

## 8. 待辦（引用審評清單）

- [x] ~~PR 合併 `codex/web-workbench-merge` → `main`~~（已完成）
- [x] ~~P0-06 身份認證 / P0-07 角色授權矩陣~~（已落地；`auth.py` + `app.py` + `tests/test_roles.py`）
- [x] ~~P1-04 評審指派 / 獨立提交 / 利益申報 / 盲評~~（已落地；`tests/test_assignments.py`，見頭部最新進度 2026-11-22）
- [ ] P0-02/03：機構級 OIDC/SSO 登入 + 個案級授權（代碼側基本身份/角色已備，機構對接仍待）
- [ ] P0-05：收件時間/回執正式化（已打地基：`received_at` 服務端記錄）
- [ ] P0-08：資料遷移腳本（舊個案補 `program_snapshot`）
- [ ] P1-05：評委最低人數在**簽發 UI**上的明確提示（後端已擋）
- [ ] P1-16：真實瀏覽器 E2E 納入 CI
- [ ] 全流程人工驗收：兩計劃 × 兩渠道、真實 OCR、工時基線
- [ ] 決定何時把安全/角色/字形/時區/評審工作流改動升版至 v1.2.0（目前維持 v1.1.0，依約不急於發版）

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
  track.py            台帳（Disbursement/PeriodicReport/管理報告；預警為純文字標記、無 emoji）
  pipeline.py         編排（region/privacy 參數、errors= 匯入、intake 注入偵測）
  region.py/privacy.py  多地區審查 / 去識別化（detect_script 支援 hant/hans/ja/ko/latin）
  script.py           字形標準化引擎（OpenCC s2hk/s2twp/t2s + 香港白名單 + 逐處定位）
  guard.py            安全護欄（注入偵測/輸出審查/不可信資料隔離）
  secrets.py          加密密鑰環（scrypt+HMAC，明文不落盤）
  auth.py             本地賬號 + HTTP Basic + 角色（P0-06/07）
  export.py           Excel 匯出
configs/program_demo.yaml     示範計劃（HK-ACC-2027）
configs/campus_pilot.yaml     校園試點計劃（第二套規則）
configs/regions/*.yaml        15 地區配置
web/                  前端（app.js/index.html/styles.css）
scripts/              check_language.py / benchmark.py / pitch_demo.py / vault_cli.py
tests/                191 項測試（含 test_roles/test_security/test_script/test_tracking/test_serve/test_traceability…）
docs/                 architecture.md / deployment.md / proposal-outline.md / RELEASE_NOTES_v1.0~v1.1
Dockerfile / docker-compose.yml  非 root 容器、/data 持久卷、健康檢查
deploy/               fly.toml / render.yaml 雲端部署範例
```

---

## 10. 接手第一天建議

1. `git switch main && git pull` → `python -m pytest tests -q`（應 159/159）；異地啟動見 `docs/deployment.md`。
2. 起 Web 工作台（臨時 DB），用不同角色賬號登入，驗證角色矩陣：operator 可收件但不可簽發、approver 才可簽發、finance 才可動台帳。
3. 載入示範申請，檢查 002 的「待複議」狀態、評審包導出、字形審計端點 `/api/script/audit` 與安全掃描 `/api/security/scan`。
4. 讀 [全面審評與改進清單](<C:/Users/25345/Documents/New project/GrantFlow_全面审评与改进清单.md>) 的 P0 表，認領剩餘的人工項（OIDC/SSO、環境分離、備份演練）或 P0-08 遷移腳本。

> 本專案的核心原則：**證據可追溯、決定可重現、正式通知由人簽發**。改動時守住這三條。
