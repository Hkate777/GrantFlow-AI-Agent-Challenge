# GrantFlow v1.0.0

「智賦香江」AI智能體應用大賽 · 指定賽道 · 場景16（教育與文化創意賽道 · 校內行政工作流方向）

參賽隊伍：KK_Garden

GrantFlow把「申請受理：資格審查：交叉驗證：評審彙總：結果通知：項目追蹤」的整條行政工作流交給智能體自動完成。此版本為大賽提交版本，對應場景16全部七項官方功能要求。

## 核心能力

- 七段Agentic Workflow：Ingest、Extract、Check、Verify、Summarize、Review、Track
- 聲明式計劃規則引擎：計劃差異收斂到YAML，新計劃零代碼接入
- 15個地區合規配置：涵蓋中國香港、中國台灣、中國內地、東北亞、英聯邦、中東、東南亞及美洲，載入真實法規框架（UK GDPR、Privacy Act、PIPEDA、APPI、PIPA、PDPL、PDPA等）
- 多模型可插拔：Mock離線可復現，亦支持OpenAI兼容端點、Anthropic Claude、Google Gemini；無密鑰時優雅降級
- 私隱去識別化：電郵、電話、身份證、銀行卡、合約編號在送模型前以不可逆佔位符取代，原始值不離開本機；對外評審包、Excel與審計日誌均無明文敏感資訊
- 全程可審計：每個Agent動作寫入審計日誌，關鍵決策（資格淘汰、利益衝突、複議）標記為需人工複核

## 對應場景16官方功能要求

| 官方要求 | 對應模塊 |
|---|---|
| 多渠道收集整合申請資料及附件 | ingest.py多格式解析 |
| 按個別計劃要求檢查完整性與合規性 | check.py規則引擎 |
| 識別缺失、重複、錯誤及異常資料 | extract.py交叉驗證與離羣檢測 |
| 生成標準化摘要、一頁式簡介及評審資料包 | summarize.py |
| 匯總評語評分並識別分歧個案 | review.py |
| 生成結果通知、待辦與項目追蹤 | review.py、track.py |
| 異常預警與管理報告 | track.py |

多地區、多模型與私隱三項增強均服務於上述要求，並非額外場景：多地區對應「各計劃要求各不相同」的跨境延伸，多模型提升非結構化材料抽取品質，私隱防護保障受理環節的資料安全。

## 驗證結果

- 10項端到端測試全部通過
- 語言規範嚴格檢查54個文件通過
- CLI、Web面板、Excel匯出、基準測試及決賽演示物料腳本實測可用
- 示範批次準確檢出預算矛盾、人數超限、預算超標、利益衝突、重複申請及預算離羣

## 快速開始

```bash
pip install -r requirements.txt
python cli.py                 # 離線跑通全流程，無需API Key
python app.py                 # Web面板
python -m pytest tests -q     # 10項測試
python scripts/pitch_demo.py  # 一鍵生成決賽演示物料
```

完整説明見README.md，架構與功能對照見docs/architecture.md，計劃書大綱見docs/proposal-outline.md。

License: MIT · Team: KK_Garden
