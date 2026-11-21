# 架構設計説明

## 1. 設計目標

場景16的核心挑戰：**各計劃要求、格式、準則各異；材料結構化與非結構化混雜；需支持評審、決策與後續管理全流程**。GrantFlow的三條設計原則：

1. **規則外置**：計劃差異收斂到YAML聲明式配置（`configs/`），代碼零改動適配新計劃。
2. **人機協同**：Agent只做「檢查與建議」，淘汰/迴避/複議等關鍵決策必須人工確認（`needs_human` 標記貫穿全鏈路）。
3. **可復現可審計**：MockLLM保證離線確定性；AuditTrail記錄每個動作與依據。

### 與場景16官方功能要求的逐條對照

以下七項均直接對應場景16「功能要求」原文，證明本項目始終在指定方向內：

| 官方功能要求 | 本項目對應模塊 | 增強功能如何服務於該要求 |
|---|---|---|
| ①自動收集整合不同渠道提交的申請資料及附件 | `ingest.py` 多格式解析 | 多模型接入（LLM抽象層）提升非結構化內容的理解與抽取品質 |
| ②按個別計劃要求檢查完整性及合規性（必填、附件、格式、資格、利益衝突） | `check.py` 規則引擎 | 規則外置YAML直接實現「個別計劃要求不同」；多地區配置是此要求在跨境語境下的延伸；私隱去識別化保障受理環節的資料安全 |
| ③自動識別缺失、重複、錯誤或異常資料 | `extract.py::cross_verify` + `anomaly_scan` | 跨源比對、批次查重與穩健離羣檢測 |
| ④生成標準化摘要、一頁式簡介及評審資料包 | `summarize.py` | 評審包在對外輸出前自動去識別化，保護申請人隱私 |
| ⑤按預設評審框架整理匯總評語與評分，識別分歧個案 | `review.py` | 維度加權、分歧閾值與複議建議 |
| ⑥生成結果通知、待辦及跟進清單，追蹤進度/資助/里程碑/定期報告 | `review.py` + `track.py` | 逾期與臨期兩級預警、資助發放進度表 |
| ⑦提供異常預警及管理報告 | `track.py` | 管理報告自動彙整，全程可審計 |

**驗收重點對應**：多格式讀取整合（①）、多來源交叉驗證（③）、完整性與資格審核準確率（②）、規則執行正確率（②）、異常檢測（③）、評審資料整理效率（④）、評語及評分彙總準確度（⑤）、後續項目追蹤（⑥）、流程自動化程度（全鏈路）、處理時間縮短幅度與人工節省（`benchmark.py` 量化報告）。

## 2. 模塊職責

### 核心 Agent（七步流水線）

| 模塊 | 職責 | 關鍵設計 |
|---|---|---|
| `ingest.py` | 多格式解析 | 每種格式獨立解析器；依賴可選、失敗不中斷（`error` 字段） |
| `extract.py` | LLM 結構化抽取 | JSON 輸出 + 解析容錯降級；Mock/真實 LLM 同接口 |
| `evidence.py` | 欄位原文佐證 | 中英日標籤別名表 + 繁簡容錯；輸出 `EvidenceSpan`（出處/引文/值） |
| `money.py` | 港幣金額解析 | 支援千/萬/百萬/億與 HK$、港元；拒絕布林與非有限數 |
| `check.py` | 完整性/合規審查 | 規則引擎按 YAML 執行；利益衝突用「評審人 → 關鍵詞」映射 |
| `extract.py::cross_verify` | 跨源比對 | 數值容差 5%；標題/郵箱雙向包含匹配 |
| `extract.py::anomaly_scan` | 批次異常 | 中位數 ± 5×MAD 穩健離羣檢測 |
| `summarize.py` | 摘要與資料包 | 模板化 + LLM 凝練；輸出 summary/one_pager/checklist |
| `review.py` | 評審彙總與狀態機 | 維度歸一百分制加權；分歧閾值觸發複議；`assess` 的 holds 順序即狀態語義 |
| `track.py` | 項目追蹤 | 逾期/臨期（14 天）兩級預警；資助發放進度表；台帳寫入校驗 |

### 支撐層（工作台、持久化、安全、編排）

| 模塊 | 職責 | 關鍵設計 |
|---|---|---|
| `pipeline.py` | 批次編排 | 階段式 Agentic Workflow，全程 AuditTrail |
| `workspace.py` | 個案生命週期 | 收件/補件/評分/複議/人工簽發/資助台帳；版本號防衝突；程序快照隨案 |
| `storage.py` | SQLite 持久化 | 串行化寫入（`BEGIN IMMEDIATE`）、版本衝突、分頁查詢 |
| `submission.py` | 安全收件 | 表單 JSON 校驗、附件數量/大小上限、`.eml` 解析、SHA-256 指紋冪等 |
| `auth.py` | 帳號與身份 | scrypt 加鹽口令、HTTP Basic、工作階段 TTL、停用即撤銷全部工作階段 |
| `llm.py` | LLM 抽象 | Protocol 接口；Mock/OpenAI 兼容/Anthropic/Gemini；`auto` 恆本地 |
| `privacy.py` | 私隱去識別化 | 送模型前遮蔽 PII，隨機 salt 防跨請求關聯，輸出閘門 `ensure_safe_output` |
| `guard.py` | 安全護欄 | 提示注入偵測（只標記不執行）、隔離包裝 `wrap_untrusted`/`harden_prompt`、輸出審查 `review_output`、數字台賬 `build_ledger`/`check_ledger` 防幻覺 |
| `explain.py` | 確定性依據卡 | 由 Finding 規則生成「原始出處／排序／理由」，零 LLM、零新數字；缺失值走 `imputed` 標記 |
| `scoring.py` | 確定性評分 | 權重和 strict 必須 1.0／relative 雙模式；逐步貢獻可稽核；缺維度不補零 |
| `eval_traces.py` | Trace 評測 | 引用完整性／來源多樣性／判定留痕三項零成本規則判據；可選 Judge 失敗不影響規則 |
| `compliance.py` | 合規分級 | refuse/degrade/note 三級 + 法規依據 + 「未命中不代表合規」能力邊界聲明 |
| `decision_journal.py` | 決策品質 | 結構化 LLM 判定、N 天回看對帳、按模型準確率（未覆核不進分母） |
| `secrets.py` | 加密密鑰環 | scrypt 派生 + HMAC 串流加密 + encrypt-then-MAC；明文不落盤、清單僅遮蔽值 |
| `script.py` | 字形標準化 | OpenCC 逐處定位、香港標準字白名單、冪等轉換 |
| `program.py` | 計劃規則模型 | YAML 載入；`from_snapshot` 從個案快照重建規則 |
| `region.py` | 地區模型 | 字形檢測；15 地區配置載入；地區當地日期 |
| `timezone.py` | 地區時區 | IANA 時區映射；以當地今天判斷到期與確認日期；缺 tzdata 時降級 |
| `export.py` | Excel 匯出 | 三個工作表（總覽/發現明細/評分明細），敏感欄位脫敏 |

## 3. 數據流

系統有兩個入口，共用同一套規則引擎與狀態機：

**入口一：命令行批次全流程**（`cli.py` → `Pipeline`）

```
申請目錄（meta.json + 附件）
  → load_applications → Application(docs=[Document...])
  → intake → extracted: dict + findings: [Finding]
  → batch_checks → 批次級查重 / 離羣發現
  → build_packs → 評審資料包目錄（對外一律去識別化）
  → review（消費 ReviewScore[]）→ assess 狀態機 + 評審進度草稿 + 待辦
  → track（消費 Milestone[]）→ 管理報告
```

所有中間產物落盤（Markdown/JSON），天然支持「斷點續跑」與人工介入。命令行只產出評審進度草稿（`review_draft`），不發正式通知。

**入口二：Web 工作台個案生命週期**（`app.py` → `Workspace`）

```
submit（表單/.eml/資料夾）→ 建立個案（含計劃快照 + 服務端收件時間）
  → _evaluate → 完整性/資格/截止/規則版本/批次發現 → assess 狀態機
  → amend（補件，清空舊評分）→ score（評分，校驗維度與人數下限）
  → resolve（記錄複議/人工覆核，綁定評估 basis）
  → sign（人工簽發，鎖定規則快照 + 生成正式結果通知）
  → track（資助台帳：里程碑/撥款/定期報告，攢款不得超核准額）
```

每次操作都做版本號檢查（`revision`），過期操作被拒絕；`_evaluate` 對已簽發個案直接回傳簽發時的 assessment 與 findings，保證歷史結果不隨規則更新而漂移。

**安全閘門（貫穿兩個入口）**：申請內容一律視為不可信輸入。`intake` 在**任何模型呼叫之前**先以 `guard.scan_prompt_injection` 偵測注入（規則＋結構化啟發式；命中轉 `FindingType.SECURITY`，`needs_human=True`，只記類型與已去識別化摘錄，絶不執行內容；**只作提示，不保證攔截**）；送模型前 `privacy.Redactor` 去識別化、`guard.wrap_untrusted` 隔離包裹、`harden_prompt` 附加最高優先隔離宣告；模型輸出經 `guard.review_output` 審查，可疑即整批不採用。MockLLM 不發出模型請求，但掃描與對外閘門照樣執行。抽取結果不再 `restore()` 回明文，交叉驗證使用去識別化視圖。

**對外物料出口**：`output_gate.py` 是 `ensure_safe_output()` 的實際呼叫點——Web 評審包、CLI 評審包、Excel 總表與結果通知必經該層，命中類型與次數寫入 `gate_report.json`。結果通知另以 `build_ledger(app, assessment, budget_cap)` 建立可信數字台賬，`gate_text(ledger=...)` 檢查通知中台賬外的金額／分數並記入 `數字幻覺` 統計與審計事件。身份方面，Basic 已驗證憑據有短時記憶體快取（停用即清空）；遠端模式停用弱口令演示賬號，評分身份綁定登入帳號，利益衝突以 `subject_id`/`reviewer_id` 結構化比對（舊記錄回退至姓名正規化）。API 憑據由 `secrets.SecretVault` 加密存放，`llm._key()` 優先環境變數、其次密鑰環；明文不進日誌、不落盤，也**不再注入 `os.environ`**。

**可追溯鏈（Finding → 依據卡 → 評測 → 決策品質）**：`Pipeline.traceability()` 把每條 Finding 轉為 `explain.EvidenceCard`（原始出處／排序／規則理由，零 LLM），`eval_traces.rule_checks()` 對卡片做引用完整性／來源多樣性／判定留痕三項零成本檢查（LLM-as-Judge 僅附加、失敗不影響規則），`compliance.classify()` 給出 refuse/degrade/note 三級合規分級（每條帶法規依據並附「未命中不代表合規」聲明），`scoring.deterministic_score()` 提供可稽核的確定性評分（缺維度不補零），`decision_journal` 持久化判定並支援 N 天回看與按模型準確率。CLI 在 `[7/7]` 把全部物料寫入 `output/traceability/`；全部為規則式、可重現，是「可驗證／可解釋」的落實而非文件口號。

## 4. 多地區審查

Region模型以yaml描述各地語言、法規、格式及評審慣例。Pipeline接受region參數，intake階段執行check_region，輸出語言一致性、法規關鍵詞、本地用語與貨幣格式的提示。CLI以`--region`切換，Web面板提供下拉選單；新增地區只需加入一份yaml。

**與場景16的關係**：場景16指出「各計劃的申請文件要求、提交格式、評審準則及資格條件均不相同」。大學、科技園及資助機構受理的計劃往往來自不同國家與地區，其申請語文、私隱法規與文件格式各異。多地區能力正是「個別計劃要求各不相同」在跨境與多語境下的延伸——它讓同一套Agent按計劃來源地切換審查視角，仍服務於「完整性與合規性檢查」這一核心要求，而非獨立的出海工具。

目前配置15個地區：中國香港(HK)、中國台灣(TW)、中國內地(CN)、新加坡(SG)、美國(US)、英國(GB)、澳洲(AU)、加拿大(CA)、日本(JP)、韓國(KR)、阿聯酋(AE)、沙特(SA)、馬來西亞(MY)、泰國(TH)、埃及(EG)。

**地區時區**：每份地區配置含 `timezone`（IANA 時區，如香港 `Asia/Hong_Kong`、紐約 `America/New_York`）。同一瞬間各地日期可能不同，追蹤模組（里程碑到期、撥款/報告的「確認日期不可晚於今天」）一律以地區當地今天判斷，而非執行機器的本地時間——例如香港已 11-21 而紐約仍 11-20 時，兩地對各類確認日期的判定各自正確。Pipeline 追蹤階段傳入 `region.timezone`，Web/CLI 若處理含時區標記的舊個案則自動從記錄推斷；缺 tzdata 時安全降級到機器本地日期。此能力確保 agent 面對跨境申請時日期語義與地區一致。

選地覆蓋機構常見的資助計劃來源地，按語系與合規框架歸類：

| 組別 | 地區 | 依據 |
|---|---|---|
| 中國香港基本盤 | HK、TW、CN | 本地及大灣區計劃申請，繁簡與英文混用 |
| 英聯邦常規 | GB、AU、CA、SG | 英式英語與普通法體系，常見於國際合作計劃 |
| 東北亞 | JP、KR | 科研與教育合作緊密市場；日本APPI、韓國PIPA為亞洲主要私隱框架 |
| 中東 | AE、SA | 新興資助與科研合作來源，PDPL合規要求日漸重要 |
| 東南亞 | MY、TH | 職教與產學合作計劃常見來源，PDPA為主要私隱框架 |
| 其他 | US、EG | 成熟科研資助市場與新興教育合作市場 |

各地配置載入真實合規要求，例如英國UK GDPR與Data Protection Act、澳洲Privacy Act與APP、加拿大PIPEDA、日本APPI、韓國PIPA、中東與東南亞的PDPL及PDPA；Agent按地區自動切換審查視角，無需修改程式。所有地區檢查均為建議性提示，由人工確認適用性。

## 5. 人工簽發與狀態機

這是本項目「負責任AI」的核心：智能體只做檢查與建議，**正式結果通知永不由系統自動發出**。

**holds 順序即狀態語義**（`review.assess`）：按固定優先級把未解決事項排序，`status = holds[0]`：

```
pending_deliberation（待複議）
  → needs_materials（待補件）
  → needs_compliance（資格待核）
  → invalid_scores（評分待更正）
  → pending_scores（待評分）
  → insufficient_reviews（評審人數不足）
  → needs_review（待人工覆核）
```

調整順序會改變個案首選狀態與測試，必須同步更新。無 holds 時狀態為 `awaiting_decision`（待簽發），此時才允許人工簽發。

**六條關鍵契約**：

1. **通知由人簽發**：`assess()` 的 `notice` 恆為 `None`；CLI/Excel 寫的是 `review_draft()` 評審進度草稿，正式通知只在 `workspace.sign()` 時生成。
2. **截止以收件時間為準**：用服務端 `received_at` 對比計劃截止日，不採信申請人自報的 `submitted_at`，逾期申請阻斷簽發。
3. **評審人數下限**：`Program.min_reviewers`（YAML 可配，兩個內置計劃均為 2）；不足產生 `insufficient_reviews` hold，簽發被擋。
4. **程序快照隨案**：個案保存 `program_snapshot`，`sign()` 時把簽發版本寫入 decision；配置更新或移除後，歷史個案的評審包仍按簽發時規則重建，避免「V1 清單配 V2 評估」。
5. **版本號防衝突**：每次操作帶 `revision`，過期操作被拒絕；`amend()` 補件會清空舊評分與複議記錄，避免舊分數套新資料。
6. **`auto` 恆本地**：`get_llm('auto')` 永遠用 MockLLM；外部模型須顯式指定 `--llm` 且環境有密鑰，Web 預設不出境。

## 6. 擴展點

- **新格式**：在 `ingest._EXT_MAP` + 新增解析函數即可。
- **新計劃**：複製 `configs/program_demo.yaml` 改配置。
- **新規則**：`check_completeness` 為純函數，便於單測與擴展。
- **新地區**：在 `configs/regions/` 加入一份 yaml。
- **MCP封裝**：各Agent均為無狀態純函數，可直接包為MCP工具供跨賽道複用。
