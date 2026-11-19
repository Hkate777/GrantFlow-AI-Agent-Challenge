# 架構設計説明

## 1. 設計目標

場景16的核心挑戰：**各計劃要求、格式、準則各異；材料結構化與非結構化混雜；需支持評審、決策與後續管理全流程**。GrantFlow的三條設計原則：

1. **規則外置**：計劃差異收斂到YAML聲明式配置（`configs/`），代碼零改動適配新計劃。
2. **人機協同**：Agent只做「檢查與建議」，淘汰/迴避/複議等關鍵決策必須人工確認（`needs_human` 標記貫穿全鏈路）。
3. **可復現可審計**：MockLLM保證離線確定性；AuditTrail記錄每個動作與依據。

## 2. 模塊職責

| 模塊 | 職責 | 關鍵設計 |
|---|---|---|
| `ingest.py` | 多格式解析 | 每種格式獨立解析器；依賴可選、失敗不中斷（`error` 字段） |
| `extract.py` | LLM 結構化抽取 | JSON 輸出 + 解析容錯降級；Mock/真實 LLM 同接口 |
| `check.py` | 完整性/合規審查 | 規則引擎按 YAML 執行；利益衝突用「評審人至關鍵詞」映射 |
| `extract.py::cross_verify` | 跨源比對 | 數值容差 5%；標題/郵箱雙向包含匹配 |
| `extract.py::anomaly_scan` | 批次異常 | 中位數 ± 5×MAD 穩健離羣檢測 |
| `summarize.py` | 摘要與資料包 | 模板化 + LLM 凝練；輸出 summary/one_pager/checklist |
| `review.py` | 評審彙總 | 維度歸一百分制加權；總分差 ≥ 閾值觸發複議 |
| `track.py` | 項目追蹤 | 逾期/臨期（14 天）兩級預警；資助發放進度表 |
| `pipeline.py` | 編排 | 階段式 Agentic Workflow，全程 AuditTrail |
| `llm.py` | LLM 抽象 | Protocol 接口；Mock（離線啓發式）/ OpenAI 兼容端點 |

## 3. 數據流

申請目錄（meta.json + 附件）至 `load_applications` 至 `Application(docs=[Document...])`
至intake產出 `extracted: dict` + `findings: [Finding]`
至build_packs產出評審資料包目錄
至review消費 `ReviewScore[]` 產出加權結果/通知/待辦
至track消費 `Milestone[]` 產出管理報告

所有中間產物落盤（Markdown/JSON），天然支持「斷點續跑」與人工介入。

## 4. 多地區審查

Region模型以yaml描述各地語言、法規、格式及評審慣例。Pipeline接受region參數，intake階段執行check_region，輸出語言一致性、法規關鍵詞、本地用語與貨幣格式的提示。CLI以`--region`切換，Web面板提供下拉選單。目前配置涵蓋HK、TW、CN、SG、US；新增地區只需加入一份yaml。

## 5. 擴展點

- **新格式**：在 `ingest._EXT_MAP` + 新增解析函數即可。
- **新計劃**：複製 `configs/program_demo.yaml` 改配置。
- **新規則**：`check_completeness` 為純函數，便於單測與擴展。
- **MCP封裝**：各Agent均為無狀態純函數，可直接包為MCP工具供跨賽道複用。
