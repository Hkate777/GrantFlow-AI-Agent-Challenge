# GrantFlow 部署與網絡防護指南

本文件説明如何把工作台從本機演示搬到容器或雲端，並説明隨附的防護機制與上線檢查清單。所有設定均可透過環境變數完成，不需修改程式碼。

## 一、四種啟動方式

| 方式 | 指令 | 適用場景 |
|---|---|---|
| 本機 | `./scripts/start.sh local` 或 `python app.py` | 開發、演示、決賽現場 |
| 遠端程序 | `./scripts/start.sh remote` | 已有 HTTPS 反向代理的虛擬機 |
| 容器 | `./scripts/start.sh docker` | 本機或自建伺服器一鍵啟動 |
| 雲端 | 複製 `deploy/fly.toml` 或 `deploy/render.yaml` 後部署 | 需在異地隨時啟動、自動重啟 |

本機模式只監聽 `127.0.0.1`，即使忘記設防火牆也不會直接暴露到公網。遠端與容器模式把監聽地址改為 `0.0.0.0`，必須同時設定 `GRANTFLOW_ALLOWED_HOSTS` 為實際對外域名。**遠端模式不會建立弱口令演示賬號**（demo/reviewer_demo/approver_demo/finance_demo）；若沿用本機資料庫，啟動時會自動停用這些賬號，並由 `GRANTFLOW_BOOTSTRAP_PASSWORD` 建立或保留管理員。

容器資料寫入具名卷 `grantflow-data`，掛載點 `/data`；`grantflow.sqlite3`（個案）與 `auth.sqlite3`（帳號與工作階段）都在該目錄，重建容器不會遺失資料。備份時直接複製卷內檔案，並停止服務後操作以確保一致性。

## 二、環境變數

| 變數 | 説明 | 預設 |
|---|---|---|
| `GRANTFLOW_HOST` | 監聽地址 | `127.0.0.1` |
| `GRANTFLOW_PORT` | 監聽埠 | `8000` |
| `GRANTFLOW_DB` | 個案資料庫路徑 | `output/grantflow.sqlite3` |
| `GRANTFLOW_BOOTSTRAP_PASSWORD` | 首次啟動的管理員口令；留空即隨機產生並列印一次 | 空 |
| `GRANTFLOW_VAULT_PASS` | 加密密鑰環主口令；不設定則無法讀取已存憑據 | 空 |
| `GRANTFLOW_ALLOWED_HOSTS` | Host 白名單，以逗號分隔；`*` 表示不限制（不建議正式使用） | `127.0.0.1,localhost,testserver` |
| `GRANTFLOW_ALLOWED_CIDRS` | 可選 IP 網段白名單，以逗號分隔 | 空 |
| `GRANTFLOW_TRUST_PROXY` | 只在可信反向代理後設為 `true` | `false` |
| `GRANTFLOW_HTTPS_ONLY` | 加入 HSTS 響應頭；須確保前端已啟用 HTTPS | `false` |
| `GRANTFLOW_RATE_LIMIT_PER_MIN` | 每地址每分鐘最多請求數 | `120` |
| `GRANTFLOW_AUTH_FAIL_LIMIT` | 連續 401/403 達此數即封鎖 | `10` |
| `GRANTFLOW_BAN_MINUTES` | 封鎖持續分鐘數 | `15` |
| `GRANTFLOW_PUBLIC_ORIGINS` | 額外允許的寫入 Origin；留空即只接受同源 | 空 |
| `GRANTFLOW_CASE_SCOPE` | 設為 `1` 才啟用個案級授權（讀寫皆過濾）；不設則授權表等於未啟用 | `false` |

數值變數會被夾在合理範圍內，例如連線埠限制在 1 至 65535，避免誤設造成服務異常。

啟用 `GRANTFLOW_CASE_SCOPE=1` 時，授權表（`GET/PUT /api/access/grants`）須包含**所有需要寫操作的帳號**，不限評委：經辦要收件指派、主管要簽發、財務要登記台帳，任一角色未列入，其讀寫都會收到「找不到個案」。這是個案存在但無權查看時刻意與不存在同碼的設計，避免外界枚舉個案，不是權限故障。管理員不受白名單約束。排查時先以該帳號登入 `GET /api/auth/me` 確認 `case_scope` 為 `true`，再請管理員檢查 `PUT /api/access/grants` 是否漏登該帳號。工作台在啟用個案授權而目前帳號零可見個案時，列表頁會顯示對應提示，說明從何處查起。

## 三、雲端範例

`deploy/fly.toml`：香港區域、持久卷 `/data`、強制 HTTPS、`/healthz` 檢查、至少一台機器常駐。使用前把 `app` 名稱與 `GRANTFLOW_ALLOWED_HOSTS` 換成自己的域名，建立同名卷後執行 `fly deploy`。

`deploy/render.yaml`：Render Blueprint，含磁碟掛載與健康檢查路徑。需選用支援持久磁碟的方案；`GRANTFLOW_BOOTSTRAP_PASSWORD` 與 `GRANTFLOW_VAULT_PASS` 以 `sync: false` 要求在後台手動填入，不會寫入版本庫。

兩個範例都把 `GRANTFLOW_TRUST_PROXY` 設為 `true`，因為平台負載均衡器會附加轉發標頭；若改為直接在公網 IP 上運行 Python 程序，必須改回 `false`。

## 四、HTTPS 反向代理範例

以下為 Nginx 片段，讓公網流量以 HTTPS 進入，再轉發到本機 8000 埠：

```nginx
server {
    listen 443 ssl http2;
    server_name grantflow.example.edu.hk;

    ssl_certificate     /etc/letsencrypt/live/grantflow.example.edu.hk/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/grantflow.example.edu.hk/privkey.pem;

    client_max_body_size 12m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

對應環境變數：

```bash
export GRANTFLOW_HOST=127.0.0.1
export GRANTFLOW_ALLOWED_HOSTS=grantflow.example.edu.hk
export GRANTFLOW_TRUST_PROXY=true
export GRANTFLOW_HTTPS_ONLY=true
```

Origin 檢查會以 `X-Forwarded-Proto` 與 `X-Forwarded-Host` 還原對外地址，因此瀏覽器提交的 `https://grantflow.example.edu.hk` 會被視為同源；若兩者不一致，寫入操作會被拒絶。若前端使用另一個域名，可把它加入 `GRANTFLOW_PUBLIC_ORIGINS`。

## 五、防護機制

1. **Host 與 Origin 約束**：`TrustedHostMiddleware` 擋掉偽造 Host；所有非讀取請求必須來自同源或已列出的 Origin。
2. **可選 IP 網段白名單**：`GRANTFLOW_ALLOWED_CIDRS` 適合校內或機構內網服務；不在清單的地址直接回 403。
3. **速率限制**：以單調時鐘實作滑動窗口，每地址獨立計數；超限回 429 並附 `Retry-After`。健康端點不計入。
4. **失敗封鎖**：連續 401/403 達閾值即臨時封鎖；封鎖期內即使口令正確也先回 429，期滿自動解除。
5. **安全響應頭**：CSP、`X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Permissions-Policy`、Cross-Origin 標頭，以及HTTPS啟用時的 HSTS。
6. **安全日誌**：`output/security.log` 以 JSON 行記錄事件、時間、地址、路徑與狀態碼；不含請求本文、授權頭或口令，並對 `password=`/`token=` 等形式再遮蔽。
7. **代理標頭信任邊界**：只有 `GRANTFLOW_TRUST_PROXY=true` 才讀取 `X-Forwarded-For`，且取最右值（由最後一跳代理追加），降低客戶端偽造的風險。
8. **請求大小上限**：表單與附件沿用收件層的檔案上限，超額請求在進入業務邏輯前回 413。
9. **身份與輸出**：Basic 已驗證憑據有 5 分鐘記憶體快取以避免每請求重算 scrypt，停用賬號即時清空；所有對外物料（評審包、Excel、通知）必經 `output_gate.py` 的 `ensure_safe_output()`。注入偵測只作提示、不保證攔截，仍須人工覆核。
10. **密鑰邊界**：`vault_cli.py keys` 只列出可用憑據名稱；模型客戶端直接向密鑰環取值，明文不寫入 `os.environ`，避免從 `/proc/<pid>/environ` 外洩。

以上防護攔截的是常見的探測、暴力嘗試與資源消耗攻擊。它不是 WAF 的替代品；正式服務仍應配合防火牆、平台層 DDoS 防護與及時更新依賴。

## 六、健康檢查與事故處理

- `GET /healthz`：進程存活即回 200 與版本號；容器健康檢查與平台探測使用此端點。
- `GET /readyz`：實際執行一次資料庫讀取；失敗回 503，讓負載均衡器暫停派流量。
- 兩者都不需要登入，也不記錄業務資料；若健康檢查失敗，先查看容器日誌與資料庫掛載權限。
- 大量 429：檢查是否被反向代理還原成同一地址（例如未在代理設定 `X-Forwarded-For`），再考慮調高 `GRANTFLOW_RATE_LIMIT_PER_MIN`。
- 大量 403：確認 Host、Origin 與 CIDR 設定是否與實際域名／網段一致。
- 忘記管理員口令：停止服務後刪除 `auth.sqlite3` 並重新啟動，系統會以 `GRANTFLOW_BOOTSTRAP_PASSWORD` 重建初始帳號；此操作會清除所有本機帳號與工作階段，個案資料不受影響。

## 七、上線檢查清單

- [ ] `GRANTFLOW_HOST` 與 `GRANTFLOW_ALLOWED_HOSTS` 已按實際部署環境設定。
- [ ] HTTPS 已由平台或反向代理強制啟用，`GRANTFLOW_HTTPS_ONLY=true`。
- [ ] 演示帳號已停用，管理員口令由環境變數提供且足夠強度。
- [ ] 持久化卷已掛載，`grantflow.sqlite3` 與 `auth.sqlite3` 有備份。
- [ ] `GRANTFLOW_VAULT_PASS` 已設定，模型憑據只經密鑰環寫入。
- [ ] `/healthz` 與 `/readyz` 在平台可達，告警已接管異常狀態。
- [ ] `output/security.log` 有輪替或集中收集機制。
- [ ] 若服務對象固定，已設定 `GRANTFLOW_ALLOWED_CIDRS`。
