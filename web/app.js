"use strict";

const icons = {
  brand: "M4 6.5 12 3l8 3.5-8 3.5L4 6.5Zm0 5 8 3.5 8-3.5m-16 5 8 3.5 8-3.5",
  tray: "M4 5h16l2 9v4a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-4l2-9Zm-2 9h6l1.5 2h5l1.5-2h6",
  list: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
  clock: "M12 7v5l3 2m6-2a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  pin: "M8 4h8l-1 5 3 3v1H6v-1l3-3-1-5Zm4 9v7",
  check: "m8 12 2.5 2.5L16 9m5 3a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  minus: "M8 12h8m5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  mail: "M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Zm-1 2 9 6 9-6",
  plus: "M12 5v14M5 12h14",
  right: "m9 5 7 7-7 7",
  down: "m5 9 7 7 7-7",
  search: "M15 15l5 5m-3-9a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z",
  briefcase: "M3 8h18v12H3V8Zm6 0V4h6v4M3 13h18m-9-1v3",
  download: "M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4",
  reload: "M20 7v5h-5M4 17v-5h5m11-2a8 8 0 0 0-14-4m-2 8a8 8 0 0 0 14 4",
};

const statuses = {
  needs_materials: ["待補件", "warning"],
  needs_compliance: ["待合規核查", "error"],
  needs_review: ["待人工覆核", "warning"],
  pending_scores: ["待評分", "info"],
  invalid_scores: ["待更正評分", "error"],
  insufficient_reviews: ["評審人數不足", "warning"],
  pending_deliberation: ["待複議", "warning"],
  awaiting_decision: ["待批核", "info"],
  approved: ["已批核", "success"],
  declined: ["不獲批核", "error"],
};

const queues = [
  { key: "all", label: "全部個案", icon: "list", matches: () => true },
  { key: "active", label: "處理中", icon: "clock", matches: (status) => !["awaiting_decision", "approved", "declined"].includes(status) },
  { key: "awaiting_decision", label: "待批核", icon: "pin", matches: (status) => status === "awaiting_decision" },
  { key: "approved", label: "已批核", icon: "check", matches: (status) => status === "approved" },
  { key: "declined", label: "不獲批核", icon: "minus", matches: (status) => status === "declined" },
];

const tabs = {
  overview: "個案資料",
  review: "評審與批核",
  trace: "可追溯",
  project: "項目管理",
  audit: "操作紀錄",
};

const channelLabels = { web: "網上表單", email: "電郵", mail: "電郵", demo: "示範" };
const findingLabels = {
  missing: "欠缺資料", duplicate: "疑似重複", format: "格式問題",
  eligibility: "資格核查", conflict: "潛在利益衝突", anomaly: "異常", inconsistent: "資料不一致",
};
const severityLabels = {
  info: ["提示", "info"], warning: ["警告", "warning"], error: ["需處理", "error"], critical: ["嚴重", "error"],
};
const docLabels = { proposal: "申請書", budget: "預算表", cv: "團隊簡歷", endorsement: "推薦信", demo: "示範影片" };
const auditLabels = {
  intake: "收件", imported: "匯入申請", demo_import: "匯入示範個案", materials_updated: "更新申請資料",
  scores_updated: "更新評分", resolution_recorded: "登記人工覆核", decision_recorded: "登記批核結果", project_updated: "更新項目進度",
};

const state = {
  programs: [], cases: [], listReady: false, demoBusy: false,
  route: null, activeCase: null, caseRequest: 0, registryRequest: 0,
  queue: "all", programFilter: "", statusFilter: "", query: "", rulesOpen: false,
  tab: "overview", editor: null, keepNotice: false,
};

const content = document.querySelector("#workspace");
const rail = document.querySelector("#queue-nav");
const breadcrumbs = document.querySelector("#breadcrumbs");
const notices = document.querySelector("#notices");
const demoButton = document.querySelector("#load-demo");
const dateTime = new Intl.DateTimeFormat("zh-HK", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hourCycle: "h23", timeZone: "Asia/Hong_Kong",
});
const decimal = new Intl.NumberFormat("zh-HK", { maximumFractionDigits: 2 });
const money = new Intl.NumberFormat("zh-HK", { style: "currency", currency: "HKD", currencyDisplay: "code" });
let nextControlId = 0;

function el(tag, className = "", ...children) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  for (const child of children) {
    if (child !== null && child !== undefined) element.append(child);
  }
  return element;
}

function icon(name) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(namespace, "path");
  path.setAttribute("d", icons[name]);
  svg.append(path);
  return svg;
}

function button(label, action, kind = "secondary", glyph) {
  const control = el("button", `button ${kind}`, glyph ? icon(glyph) : null, label);
  control.type = "button";
  if (action) control.addEventListener("click", action);
  return control;
}

function link(label, href, className = "", glyph) {
  const anchor = el("a", className, glyph ? icon(glyph) : null, label);
  anchor.href = href;
  return anchor;
}

function valueText(value) {
  if (value === undefined || value === null || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(valueText).join("、") : "—";
  if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key} ${valueText(item)}`).join("、") || "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

function timeText(value) {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : dateTime.format(date);
}

function numberText(value) {
  return typeof value === "number" && Number.isFinite(value) ? decimal.format(value) : "—";
}

function statusLabel(code) {
  return statuses[code]?.[0] || code || "未有狀態";
}

function badge(label, tone = "") {
  const dot = el("span", "status-dot");
  dot.setAttribute("aria-hidden", "true");
  return el("span", `status ${tone}`, dot, label);
}

function statusBadge(code) {
  return badge(statusLabel(code), statuses[code]?.[1]);
}

function list(items, className = "plain-list") {
  return el("ul", className, ...items.map((item) => el("li", "", item)));
}

function table(columns, rows, className = "") {
  const cells = columns.map(([label, key]) => {
    const heading = el("th", key, label);
    heading.scope = "col";
    return heading;
  });
  return el("div", "table-wrap", el("table", `data-table ${className}`,
    el("thead", "", el("tr", "", ...cells)),
    el("tbody", "", ...rows.map((row) => el("tr", "", ...row.map((cell, index) => el("td", columns[index][1], cell))))),
  ));
}

function panel(title, body, action = null) {
  return el("section", "panel", el("div", "panel-heading", el("h2", "", title), action), body);
}

function pairs(values, className = "data-pairs") {
  return el("dl", className, ...values.map(([label, value]) => el("div", "", el("dt", "", label), el("dd", "", valueText(value)))));
}

function programFor(id) {
  return state.programs.find((program) => program.program_id === id);
}

function channelText(channel) {
  return channelLabels[channel] || channel || "—";
}

function casePath(id) {
  return `/api/cases/${encodeURIComponent(id)}`;
}

function caseHref(id) {
  return `#/case/${encodeURIComponent(id)}`;
}

function queueHref(queue) {
  return queue === "all" ? "#/cases" : `#/cases/${queue}`;
}

function safeDecode(text) {
  try { return decodeURIComponent(text); } catch { return ""; }
}

function readRoute() {
  const [view, key] = location.hash.slice(2).split("/");
  if (view === "case" && key) return { view, id: safeDecode(key) };
  if (view === "new" || view === "mail") return { view };
  return { view: "list", queue: queues.some((queue) => queue.key === key) ? key : "all" };
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, { ...options, headers: { Accept: "application/json", ...options.headers } });
  } catch {
    throw new Error("無法連接本機伺服器。請確認程式正在執行後重試。");
  }
  const data = await response.json().catch(() => null);
  if (response.ok && data !== null) return data;
  const detail = data?.detail;
  const description = Array.isArray(detail) ? detail.map((item) => item.msg || valueText(item)).join("。") : detail;
  const message = response.status === 401
    ? "請先登入：使用管理員發出的賬號與口令；忘記憑據請聯絡系統管理員。"
    : response.status === 409
    ? "資料版本已有更新。請重新載入個案，再提交變更。"
    : description || (response.ok ? "伺服器傳回了無法讀取的資料。" : `操作失敗（${response.status}）。請重試。`);
  const error = new Error(message);
  error.status = response.status;
  throw error;
}

function jsonRequest(path, method, body) {
  return api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function messageBox(text, tone, action = null) {
  return el("div", `message ${tone}`, el("span", "block-text", text), action);
}

function notice(text, tone = "", keep = false) {
  const dismiss = button("關閉", () => notices.replaceChildren(), "quiet");
  dismiss.setAttribute("aria-label", "關閉通知");
  state.keepNotice = keep;
  notices.replaceChildren(messageBox(text, tone, dismiss));
}

function loading(title, text) {
  const label = el("p", "loading-label", text);
  label.setAttribute("role", "status");
  const placeholder = el("div", "skeleton");
  placeholder.setAttribute("aria-hidden", "true");
  content.setAttribute("aria-busy", "true");
  content.replaceChildren(el("h1", "", title), label, placeholder);
}

function showFailure(title, error, retry) {
  const alert = messageBox(error.message, "error", button("重試", retry));
  alert.setAttribute("role", "alert");
  content.setAttribute("aria-busy", "false");
  content.replaceChildren(el("header", "page-heading", el("h1", "", title)), alert);
}

function renderRail() {
  const links = queues.map((queue) => {
    const anchor = link(queue.label, queueHref(queue.key), "rail-link", queue.icon);
    if (state.route?.view === "list" && queue.key === state.queue) anchor.setAttribute("aria-current", "page");
    if (state.listReady) {
      const count = state.cases.filter((record) => queue.matches(record.status)).length;
      const amount = el("span", "rail-count", String(count));
      amount.setAttribute("aria-label", `${count} 宗`);
      anchor.append(amount);
    }
    return anchor;
  });
  rail.replaceChildren(el("span", "rail-label", "個案進度"), ...links);
  rail.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function renderBreadcrumbs() {
  const { view } = state.route;
  const home = link("個案登記冊", queueHref(state.queue));
  if (view === "list") {
    const label = el("span", "", "個案登記冊");
    label.setAttribute("aria-current", "page");
    breadcrumbs.replaceChildren(label);
    return;
  }
  const label = view === "case" ? state.activeCase?.app_id || "個案資料" : view === "new" ? "新增申請" : "匯入電郵";
  const current = el("span", "", label);
  current.setAttribute("aria-current", "page");
  breadcrumbs.replaceChildren(home, icon("right"), current);
}

function viewTitle(title, detail = null, actions = null) {
  return el("header", "page-heading", el("div", "", el("h1", "", title), detail ? el("p", "lead", detail) : null), actions);
}

function show(...nodes) {
  content.setAttribute("aria-busy", "false");
  content.replaceChildren(...nodes);
}

function selectControl(choices, value, label) {
  const control = el("select");
  for (const [optionValue, optionLabel] of choices) {
    const option = el("option", "", optionLabel);
    option.value = optionValue;
    control.append(option);
  }
  control.value = value;
  if (label) control.setAttribute("aria-label", label);
  return control;
}

function programBanner(program) {
  const details = el("details", "program-banner");
  details.open = state.rulesOpen;
  details.addEventListener("toggle", () => { state.rulesOpen = details.open; });
  const summary = el("summary", "",
    el("div", "program-identity", el("span", "program-icon", icon("briefcase")),
      el("div", "", el("strong", "program-name", program.name),
        el("span", "program-meta number", program.deadline ? `截止 ${timeText(program.deadline)}` : "未設截止日期"))),
    el("span", "program-expand", "計劃準則", icon("down")),
  );
  const rules = [
    ["必填欄位", (program.required_fields || []).map((field) => field.label || field.key)],
    ["附件要求", (program.required_docs || []).map((doc) => {
      const title = docLabels[doc.name] ? `${docLabels[doc.name]}（${doc.name}）` : doc.name;
      return `${title}${doc.required ? " · 必須" : " · 建議"}${doc.doc_type === "any" ? "" : ` · ${doc.doc_type}`}`;
    })],
    ["評審準則", (program.criteria || []).map((criterion) => `${criterion.name} · 滿分 ${criterion.max_score} · 權重 ${criterion.weight}`)],
  ];
  const items = rules.map(([label, values]) => el("div", "", el("h3", "", label),
    values.length ? list(values, "rule-list") : el("p", "hint", "未有設定")));
  details.append(summary, el("div", "program-rules", ...items));
  return details;
}

function registerRows(target, count) {
  const queue = queues.find((item) => item.key === state.queue) || queues[0];
  const query = state.query.trim().toLocaleLowerCase("zh-HK");
  const records = state.cases.filter((record) => queue.matches(record.status)
    && (!state.programFilter || record.program_id === state.programFilter)
    && (!state.statusFilter || record.status === state.statusFilter)
    && (!query || [record.app_id, record.title, record.applicant, record.program_id].some((value) => String(value || "").toLocaleLowerCase("zh-HK").includes(query))));
  count.textContent = `${records.length} 宗個案`;
  if (!records.length) {
    const empty = !state.cases.length;
    const filtered = Boolean(state.query || state.statusFilter || state.programFilter || state.queue !== "all");
    const actions = el("div", "actions");
    if (empty) {
      actions.append(link("新增申請", "#/new", "button primary", "plus"), button("匯入示範個案", importDemo));
    } else if (filtered) {
      actions.append(button("清除篩選", () => {
        state.statusFilter = "";
        state.programFilter = "";
        state.query = "";
        if (state.queue === "all") {
          renderRegister();
          content.querySelector("[type=search]").focus();
        } else {
          location.hash = queueHref("all");
        }
      }));
    }
    target.replaceChildren(el("div", "empty-state", el("span", "empty-icon", icon("tray")),
      el("h2", "", empty ? "登記冊未有個案" : "沒有符合條件的個案"),
      el("p", "muted", empty ? "可用表單錄入一宗申請，或匯入一封已儲存的電郵。" : "請調整搜尋字詞或篩選條件。"), actions));
    return;
  }
  const rows = records.map((record) => {
    const identity = [record.app_id, state.programs.length > 1 && !state.programFilter ? record.program_id : null].filter(Boolean).join(" · ");
    return [
      el("div", "", link(record.title || "未命名申請", caseHref(record.id), "case-title"),
        el("span", "id-text", identity), el("span", "mobile-applicant", valueText(record.applicant))),
      valueText(record.applicant), channelText(record.channel), statusBadge(record.status), numberText(record.weighted_total),
    ];
  });
  target.replaceChildren(table([
    ["申請項目", "case-name"], ["申請機構／人", "case-applicant"], ["收件渠道", "case-channel"],
    ["狀態", "case-status"], ["加權總分", "case-score number"],
  ], rows, "case-table"));
}

function renderRegister() {
  const register = el("section", "register");
  register.setAttribute("aria-label", "個案列表");
  const search = el("input");
  search.type = "search";
  search.value = state.query;
  search.placeholder = "搜尋項目、申請人或編號";
  search.setAttribute("aria-label", search.placeholder);
  const statusOptions = Object.keys(statuses).filter((code) => "all" === state.queue || queues.find((queue) => queue.key === state.queue).matches(code));
  const filter = statusOptions.length > 1
    ? selectControl([["", "所有狀態"], ...statusOptions.map((code) => [code, statusLabel(code)])], state.statusFilter, "個案狀態") : null;
  const programSelect = state.programs.length > 1
    ? selectControl([["", "所有計劃"], ...state.programs.map((program) => [program.program_id, program.name])], state.programFilter, "資助計劃") : null;
  const count = el("span", "result-count number");
  count.setAttribute("role", "status");
  const reload = button("重新載入", () => reloadRegistry(reload), "quiet", "reload");
  const rows = el("div");
  search.addEventListener("input", () => { state.query = search.value; registerRows(rows, count); });
  filter?.addEventListener("change", () => { state.statusFilter = filter.value; registerRows(rows, count); });
  programSelect?.addEventListener("change", () => {
    state.programFilter = programSelect.value;
    state.rulesOpen = false;
    renderRegister();
    content.querySelector('[aria-label="資助計劃"]').focus();
  });
  register.append(el("div", "toolbar", el("div", "search-box", icon("search"), search), filter, programSelect, count, reload), rows);
  registerRows(rows, count);
  const program = programFor(state.programFilter) || (state.programs.length === 1 ? state.programs[0] : null);
  const title = queues.find((queue) => queue.key === state.queue).label;
  show(viewTitle(state.queue === "all" ? "申請個案" : title), program ? programBanner(program) : null, register);
}

async function reloadRegistry(control = null) {
  const request = ++state.registryRequest;
  if (control) control.disabled = true;
  try {
    const [programs, records] = await Promise.all([api("/api/programs"), api("/api/cases")]);
    if (request !== state.registryRequest) return;
    state.programs = programs;
    state.cases = records;
    state.listReady = true;
    if (!programFor(state.programFilter)) state.programFilter = "";
    renderRail();
    renderBreadcrumbs();
    if (state.route.view === "list") renderRegister();
    if (state.route.view === "new" && !control) renderNew();
  } catch (error) {
    if (request !== state.registryRequest) return;
    if (state.route.view === "list" && !state.listReady) showFailure("申請個案", error, reloadRegistry);
    else if (state.route.view === "new" && !state.listReady) showFailure("新增申請", error, reloadRegistry);
    else notice(error.message, "error");
  } finally {
    if (control) control.disabled = false;
  }
}

function demoState(busy) {
  state.demoBusy = busy;
  demoButton.disabled = busy;
  demoButton.lastElementChild.textContent = busy ? "正在匯入…" : "匯入示範個案";
}

async function importDemo(event) {
  if (state.demoBusy) return;
  const control = event.currentTarget;
  control.disabled = true;
  demoState(true);
  try {
    const result = await api("/api/demo", { method: "POST" });
    await reloadRegistry();
    notice(result.added ? `已匯入 ${result.added} 宗示範個案。` : "登記冊已有示範個案。", "");
  } catch (error) {
    notice(error.message, "error");
  } finally {
    demoState(false);
    control.disabled = false;
  }
}

const assessmentFor = (record) => record.assessment || {};
const isFinal = (record) => Boolean(record.decision) || ["approved", "declined"].includes(assessmentFor(record).status);

async function loadCase(id, focus = false, refreshList = false) {
  const request = ++state.caseRequest;
  state.activeCase = null;
  state.editor = null;
  loading("個案資料", "正在載入個案…");
  try {
    const record = await api(casePath(id));
    if (request !== state.caseRequest) return;
    state.activeCase = record;
    if (state.traceData?.id !== id) state.traceData = null;
    if (refreshList) upsertRecord(record);
    renderBreadcrumbs();
    renderCase(focus);
  } catch (error) {
    if (request !== state.caseRequest) return;
    showFailure("個案資料", error, () => loadCase(id, true, refreshList));
  }
}

function filesPanel(record) {
  const rows = (record.documents || []).map((file) => el("div", "", el("div", "line-title", el("strong", "", file.name),
    file.error ? badge("讀取失敗", "error") : null),
    el("p", "line-detail block-text", [file.doc_type, file.error].filter(Boolean).join(" · "))));
  return panel("附件", rows.length ? list(rows, "line-list") : el("p", "muted", "未有附件。"));
}

function findingsPanel(record) {
  const rows = (record.findings || []).map((finding) => {
    const [severity, tone] = severityLabels[finding.severity] || [valueText(finding.severity), ""];
    return el("div", "", el("div", "line-title", badge(severity, tone), badge(findingLabels[finding.finding_type] || finding.finding_type || "核查"),
      finding.needs_human ? badge("需人工覆核", "warning") : null),
    el("p", "block-text", finding.message),
    finding.source || finding.detail ? el("p", "line-detail block-text", [finding.source, finding.detail].filter(Boolean).join(" · ")) : null);
  });
  return panel("核查發現", rows.length ? list(rows, "line-list") : el("p", "muted", "未有核查發現。"));
}

function evidencePanel(record, program) {
  const entries = Object.entries(record.extracted?._evidence || {}).filter(([, values]) => Array.isArray(values) && values.length);
  if (!entries.length) return null;
  const groups = entries.map(([key, values]) => {
    const label = program?.required_fields?.find((field) => field.key === key)?.label || key;
    const citations = values.map((evidence) => el("div", "", el("strong", "", valueText(evidence.value)),
      el("p", "line-detail", [evidence.source, evidence.locator].filter(Boolean).join(" · ")),
      evidence.quote ? el("blockquote", "quotation block-text", evidence.quote) : null));
    return el("details", "evidence", el("summary", "", `${label}（${values.length} 項來源）`), list(citations, "line-list"));
  });
  return panel("資料來源與引文", list(groups, "line-list"));
}

function overviewView(record, program) {
  if (state.editor === "materials") return applicationForm(record);
  const fieldLabels = new Map((program?.required_fields || []).map((field) => [field.key, field.label || field.key]));
  const fields = Object.entries(record.fields || {}).filter(([key, value]) => key !== "email" || value !== record.email);
  const basic = [
    ["申請機構／人", record.applicant], ["聯絡電郵", record.email], ["團隊成員", record.team],
    ["收件時間（香港）", timeText(record.received_at)], ["收件渠道", channelText(record.channel)],
    ["規則版本", record.rule_version], ...fields.map(([key, value]) => [fieldLabels.get(key) || key, value]),
  ];
  const materialAction = !isFinal(record) ? button("補件或修訂", () => openEditor("overview", "materials")) : null;
  return el("div", "split",
    el("div", "stack", findingsPanel(record), evidencePanel(record, program)),
    el("div", "stack", panel("申請內容", pairs(basic), materialAction), filesPanel(record)),
  );
}

function scoreGrid(record, program) {
  const scores = record.scores || [];
  if (!scores.length) return el("p", "muted", "尚未登記評分。");
  const names = [...new Set([...(program?.criteria || []).map((criterion) => criterion.name),
    ...scores.flatMap((score) => Object.keys(score.criteria || {}))])];
  const columns = [["評審人", ""], ...names.map((name) => [name, "number"])];
  return table(columns, scores.map((score) => [valueText(score.reviewer), ...names.map((name) => numberText(score.criteria?.[name]))]));
}

function reviewView(record, program) {
  const assessment = assessmentFor(record);
  const final = isFinal(record);
  const actionGroup = el("div", "actions");
  if (!final && state.editor !== "scores") actionGroup.append(button(record.scores?.length ? "編輯評分" : "填寫評分", () => openEditor("review", "scores")));
  const canResolve = (assessment.holds || []).some((hold) => ["needs_review", "pending_deliberation"].includes(hold))
    || (!record.resolution && (record.findings || []).some((finding) => finding.needs_human));
  if (!final && canResolve && state.editor !== "resolution") actionGroup.append(button("登記人工覆核", () => openEditor("review", "resolution")));
  const canDecide = assessment.approval_allowed || assessment.decline_allowed;
  if (!final && canDecide && state.editor !== "decision") actionGroup.append(button("作出批核", () => openEditor("review", "decision"), "primary"));
  const totals = [["加權總分（滿分 100）", numberText(assessment.weighted_total)], ["評分差距", numberText(assessment.spread)]];
  const notes = [
    ["待辦事項", assessment.todos], ["評分提示", assessment.flags], ["評分問題", assessment.validation_errors],
  ].filter(([, items]) => items?.length).map(([label, items]) => el("div", "review-note", el("h3", "", label), list(items)));
  if (assessment.comments?.length) notes.push(el("div", "review-note", el("h3", "", "評審意見"), list(assessment.comments.map((entry) =>
    el("div", "", el("strong", "", valueText(entry.reviewer)), el("p", "block-text", valueText(entry.comment)))), "line-list")));
  const summary = panel("評審摘要", el("div", "", pairs(totals, "assessment-totals"), ...notes), actionGroup);
  const resolution = record.resolution ? panel("人工覆核記錄", pairs([["登記人", record.resolution.actor], ["覆核理由", record.resolution.reason]])) : null;
  const decision = record.decision ? panel("批核記錄", pairs([
    ["結果", statusLabel(record.decision.outcome)], ["登記人", record.decision.actor], ["理由", record.decision.reason],
  ])) : null;
  const resultNotice = assessment.notice ? panel("結果通知稿", el("p", "block-text", assessment.notice)) : null;
  let editor;
  if (state.editor === "scores") editor = scoresForm(record, program);
  if (state.editor === "resolution") editor = resolutionForm(record);
  if (state.editor === "decision") editor = decisionForm(record);
  return el("div", "stack", editor, summary, panel("個別評分", scoreGrid(record, program)), resolution, decision, resultNotice);
}

const projectStatusLabels = {
  pending: "待完成", done: "已完成", overdue: "已逾期", submitted: "已提交", received: "已提交",
  scheduled: "待發放", paid: "已發放", partial: "部分發放",
};

function projectStatus(status, fallback = "pending") {
  const code = status || fallback;
  const tone = ["done", "paid", "submitted", "received"].includes(code) ? "success" : code === "overdue" ? "error" : "";
  return badge(projectStatusLabels[code] || code, tone);
}

function moneyText(value) {
  return typeof value === "number" && Number.isFinite(value) ? money.format(value) : "—";
}

function projectView(record) {
  const approved = record.decision?.outcome === "approved" || assessmentFor(record).status === "approved";
  if (!approved) return panel("項目管理", el("p", "muted", "個案獲批後，可在此登記里程碑、資助發放和定期報告。"));
  if (state.editor === "project") return projectForm(record);
  const project = record.project || {};
  const funding = project.funding || {};
  const actions = el("div", "actions", button("更新項目進度", () => openEditor("project", "project"), "primary"),
    link("下載管理報告", `${casePath(record.id)}/report`, "button secondary", "download"));
  const alerts = project.alerts?.length ? list(project.alerts) : el("p", "muted", "未有進度預警。");
  const summary = panel("資助發放概況", el("div", "", pairs([
    ["計劃發放", moneyText(funding.scheduled)], ["實際已發放", moneyText(funding.paid)],
  ], "assessment-totals"), el("div", "review-note", el("h3", "", "進度預警"), alerts)), actions);
  const tables = [
    ["里程碑", project.milestones, [["里程碑", ""], ["到期日", "number"], ["進度", ""]], (row) => [
      valueText(row.name), timeText(row.due), projectStatus(row.status),
    ]],
    ["資助發放", project.disbursements, [["款項", ""], ["發放期限", "number"], ["計劃發放", "number"], ["已發放", "number"], ["發放日期", "number"], ["進度", ""]], (row) => [
      valueText(row.name), timeText(row.due), moneyText(row.amount), moneyText(row.paid_amount), timeText(row.paid_at),
      projectStatus(row.overdue ? "overdue" : row.status, row.paid_at ? "paid" : "scheduled"),
    ]],
    ["定期報告", project.periodic_reports, [["報告", ""], ["到期日", "number"], ["提交日期", "number"], ["進度", ""]], (row) => [
      valueText(row.name), timeText(row.due), timeText(row.submitted_at), projectStatus(row.status, row.submitted_at ? "submitted" : "pending"),
    ]],
  ];
  return el("div", "stack", summary, ...tables.map(([title, rows, columns, render]) => panel(title,
    rows?.length ? table(columns, rows.map(render)) : el("p", "muted", "未有記錄。"))));
}

const severityTone = { info: "info", warning: "warning", error: "error", critical: "error" };
const levelLabel = { refuse: "拒絕", degrade: "降級覆核", note: "提示" };
const levelTone = { refuse: "error", degrade: "warning", note: "info" };

function evidenceCards(data) {
  const cards = data.cards || [];
  if (!cards.length) return el("p", "muted", "此個案沒有可生成依據卡的核查發現。");
  const rows = cards.map((card) => {
    const basis = (card.basis || []).map((item) => el("div", "", el("strong", "", valueText(item.source || item.locator || "原始數據")),
      item.quote ? el("blockquote", "quotation block-text", item.quote) : null,
      item.value ? el("p", "line-detail", `結構化值：${valueText(item.value)}`) : null));
    return el("details", "evidence",
      el("summary", "", `${badge(card.rank_label || "依據", severityTone[card.severity] || "")} ${valueText(card.claim)}`),
      el("div", "stack",
        el("p", "", el("strong", "", "理由："), valueText(card.rationale)),
        card.imputed ? el("p", "line-detail", `推斷標記：${valueText(card.imputed_note)}`) : null,
        basis.length ? list(basis, "line-list") : null));
  });
  return panel("判定依據卡", list(rows, "line-list"),
    el("span", "hint", `${cards.length} 張 · 規則生成、零模型`));
}

function complianceView(data) {
  const report = data.compliance || {};
  const hits = report.hits || [];
  const rows = hits.map((hit) => el("div", "", el("div", "line-title",
    badge(levelLabel[hit.level] || hit.level, levelTone[hit.level] || ""), valueText(hit.rule)),
  el("p", "line-detail", valueText(hit.basis)),
  hit.excerpt ? el("blockquote", "quotation block-text", hit.excerpt) : null));
  const summary = [
    ["是否建議拒絕", valueText(report.refuse)], ["需人工覆核", valueText(report.needs_human)],
  ];
  return panel("合規三級分級", el("div", "stack", pairs(summary, "assessment-totals"),
    rows.length ? list(rows, "line-list") : el("p", "muted", "本層未命中任何規則。"),
    el("p", "line-detail", report.coverage_statement || "")),
  el("span", "hint", `${hits.length} 條命中`));
}

function scoreView(data) {
  const score = data.deterministic_score || {};
  const steps = score.steps || [];
  const rows = steps.map((step) => [valueText(step.name), numberText(step.score), numberText(step.max_score),
    numberText(step.weight), numberText(step.contribution)]);
  const tableNode = steps.length ? table([
    ["維度", ""], ["原始分", "numeric"], ["滿分", "numeric"], ["歸一化權重", "numeric"], ["百分制貢獻", "numeric"],
  ], rows) : el("p", "muted", "沒有可評分的維度。");
  const total = score.total === null || score.total === undefined ? "不完整（缺維度）" : numberText(score.total);
  return panel("確定性評分", el("div", "stack", pairs([
    ["總分", total], ["缺失維度", valueText(score.missing)],
    ["評分來源", valueText(data.score_source)], ["權重模式", valueText(score.weights?.mode)],
  ], "assessment-totals"), tableNode,
    score.weights?.issues?.length ? el("p", "line-detail", `權重提示：${valueText(score.weights.issues)}`) : null),
  el("span", "hint", "零模型 · 缺維度不補零"));
}

function evalView(data) {
  const verdicts = data.verdicts || [];
  if (!verdicts.length) return null;
  const rows = verdicts.map((verdict) => [valueText(verdict.trace_id),
    badge(verdict.rule_pass ? "規則通過" : "規則未過", verdict.rule_pass ? "ok" : "error"),
    valueText((verdict.rules || []).map((rule) => rule.name).join("、")),
    valueText(verdict.judge?.available ? verdict.judge.verdict : "不可用")]);
  return panel("Trace 評測", el("div", "stack", table([
    ["Trace", ""], ["規則判據", ""], ["檢查項", ""], ["Judge", ""],
  ], rows), el("p", "line-detail", "以規則判據為準；Judge 僅為附加訊號，失敗不影響規則結論。")),
  el("span", "hint", `${verdicts.length} 條`));
}

function traceabilityPanels(data) {
  return el("div", "stack", evidenceCards(data), complianceView(data), scoreView(data), evalView(data));
}

function traceabilityView(record) {
  const holder = el("div", "stack", el("p", "muted", "正在生成依據卡與合規分級…"));
  if (state.traceData?.id === record.id) {
    holder.replaceChildren(traceabilityPanels(state.traceData.data));
    return holder;
  }
  state.traceRequest = (state.traceRequest || 0) + 1;
  const request = state.traceRequest;
  api(`${casePath(record.id)}/traceability`).then((data) => {
    if (request !== state.traceRequest || record.id !== state.activeCase?.id) return;
    state.traceData = { id: record.id, data };
    holder.replaceChildren(traceabilityPanels(data));
  }).catch((error) => {
    if (request !== state.traceRequest) return;
    holder.replaceChildren(panel("可追溯物料", el("p", "message error", `載入失敗：${valueText(error.message || error)}`)));
  });
  return holder;
}

function auditView(record) {
  const rows = [...(record.audit || [])].reverse();
  return panel("個案操作紀錄", rows.length ? table([
    ["時間（香港）", "number"], ["操作", ""], ["操作者", ""], ["詳情", ""],
  ], rows.map((entry) => [timeText(entry.ts), valueText(auditLabels[entry.action] || entry.action), valueText(entry.actor),
    el("span", "block-text", valueText(entry.detail))])) : el("p", "muted", "未有操作紀錄。"));
}

function renderCase(focus = false) {
  const record = state.activeCase;
  const program = programFor(record.program_id);
  const assessment = assessmentFor(record);
  const header = el("header", "page-heading case-heading",
    el("div", "case-heading-main", el("p", "heading-meta", el("span", "id-text", record.app_id), el("span", "hint", program?.name || record.program_id)),
      el("h1", "", record.title || "未命名申請"), record.applicant ? el("p", "muted", record.applicant) : null),
    link("下載評審包", `${casePath(record.id)}/pack`, "button secondary", "download"));
  const holds = [...new Set(assessment.holds || [])];
  const statusbar = el("div", "case-statusbar",
    el("div", "status-line", el("span", "status-label", "個案狀態"), statusBadge(assessment.status)),
    el("div", "status-line", el("span", "status-label", "待處理事項"),
      ...(holds.length ? holds.map(statusBadge) : [el("span", "hint", "無")])));
  const tabsList = el("div", "tabs");
  tabsList.setAttribute("role", "tablist");
  tabsList.setAttribute("aria-label", "個案分頁");
  const keys = Object.keys(tabs);
  function selectTab(key) {
    state.tab = key;
    state.editor = null;
    renderCase();
    document.getElementById(`case-tab-${key}`).focus();
  }
  for (const key of keys) {
    const control = button(tabs[key], () => selectTab(key));
    control.className = "tab";
    control.id = `case-tab-${key}`;
    control.setAttribute("role", "tab");
    control.setAttribute("aria-selected", String(state.tab === key));
    if (state.tab === key) control.setAttribute("aria-controls", "case-panel");
    control.tabIndex = state.tab === key ? 0 : -1;
    tabsList.append(control);
  }
  tabsList.addEventListener("keydown", (event) => {
    const at = keys.indexOf(state.tab);
    const next = { ArrowLeft: (at + keys.length - 1) % keys.length, ArrowRight: (at + 1) % keys.length, Home: 0, End: keys.length - 1 }[event.key];
    if (next !== undefined) { event.preventDefault(); selectTab(keys[next]); }
  });
  const views = { overview: overviewView, review: reviewView, trace: traceabilityView,
    project: projectView, audit: auditView };
  const body = el("div", "case-panel", views[state.tab](record, program));
  body.id = "case-panel";
  body.setAttribute("role", "tabpanel");
  body.setAttribute("aria-labelledby", `case-tab-${state.tab}`);
  body.tabIndex = 0;
  show(header, statusbar, tabsList, body);
  if (focus) content.focus();
}

function openEditor(tab, editor) {
  state.tab = tab;
  state.editor = editor;
  renderCase();
  content.querySelector("form")?.querySelector("input, select, textarea")?.focus();
}

function closeEditor() {
  state.editor = null;
  renderCase();
  document.getElementById(`case-tab-${state.tab}`).focus();
}

function input(type = "text", value = "", options = {}) {
  const control = el(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") control.type = type;
  if (type !== "file") control.value = value ?? "";
  for (const [key, setting] of Object.entries(options)) control[key] = setting;
  return control;
}

function field(label, control, hint = "", fullWidth = false) {
  control.id = `field-${++nextControlId}`;
  const caption = el("label", "field-label", label);
  caption.htmlFor = control.id;
  const help = hint ? el("p", "hint", hint) : null;
  if (help) {
    help.id = `${control.id}-hint`;
    control.setAttribute("aria-describedby", help.id);
  }
  return el("div", fullWidth ? "field full" : "field", caption, control, help);
}

function upsertRecord(record) {
  const summary = {
    id: record.id, app_id: record.app_id, program_id: record.program_id, title: record.title,
    applicant: record.applicant, channel: record.channel, revision: record.revision,
    status: assessmentFor(record).status, weighted_total: assessmentFor(record).weighted_total,
  };
  const index = state.cases.findIndex((existing) => existing.id === record.id);
  if (index >= 0) state.cases[index] = summary;
  else state.cases.unshift(summary);
  renderRail();
}

function saveForm(title, items, options) {
  const form = el("form", "form-layout", ...items);
  const errors = el("div");
  errors.setAttribute("role", "alert");
  const submit = button(options.label, null, "primary");
  submit.type = "submit";
  const cancel = options.inCase ? button("取消", closeEditor) : link("返回個案登記冊", queueHref(state.queue), "button secondary");
  form.append(errors, el("div", "actions", submit, cancel));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    const activeRoute = state.route;
    const controls = [...form.querySelectorAll("input, select, textarea, button")].filter((control) => !control.disabled);
    errors.replaceChildren();
    try {
      const request = options.submit();
      controls.forEach((control) => { control.disabled = true; });
      form.setAttribute("aria-busy", "true");
      submit.textContent = "正在儲存…";
      const record = await request;
      upsertRecord(record);
      if (state.route === activeRoute) {
        if (activeRoute.view === "case") {
          state.activeCase = record;
          state.editor = null;
          renderBreadcrumbs();
          renderCase(true);
          notice(options.success);
        } else {
          notice(options.success, "", true);
          location.hash = caseHref(record.id);
        }
      } else {
        if (state.route.view === "list" && state.listReady) renderRegister();
        notice(options.success);
      }
    } catch (error) {
      const reload = error.status === 409 && activeRoute.view === "case" ? button("重新載入個案", () => {
        notice("已放棄表單內未儲存的變更。", "warning");
        loadCase(activeRoute.id, true, true);
      }) : null;
      errors.replaceChildren(messageBox(error.message, "error", reload));
    } finally {
      controls.forEach((control) => { control.disabled = false; });
      form.setAttribute("aria-busy", "false");
      submit.textContent = options.label;
    }
  });
  const section = panel(title, form);
  section.classList.add("form-panel");
  return section;
}

function stringList(value) {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
}

function applicationFieldControls(program, existing, email) {
  const editors = [];
  const items = [];
  for (const rule of program?.required_fields || []) {
    const current = existing[rule.key];
    if (rule.key === "email" && (current === undefined || current === email.value)) {
      editors.push([rule.key, () => email.value.trim()]);
      continue;
    }
    const kind = rule.ftype === "list" || rule.key === "description" || String(current || "").includes("\n") ? "textarea" : "text";
    const value = Array.isArray(current) ? current.join("\n") : current;
    const editor = input(kind, value, { inputMode: rule.ftype === "number" ? "decimal" : rule.ftype === "email" ? "email" : "text" });
    items.push(field(rule.label || rule.key, editor, rule.ftype === "list" ? "每行輸入一項。" : "", kind === "textarea"));
    editors.push([rule.key, () => {
      const raw = editor.value.trim();
      if (rule.ftype === "list") return stringList(raw);
      if (rule.ftype === "number" && raw && Number.isFinite(Number(raw))) return Number(raw);
      return raw;
    }]);
  }
  return { items, read: () => Object.fromEntries(editors.map(([key, read]) => [key, read()])) };
}

function applicationForm(record = null) {
  const amending = Boolean(record);
  const initialProgram = record?.program_id || state.programFilter || state.programs[0]?.program_id || "";
  const program = selectControl(state.programs.map((item) => [item.program_id, item.name]), initialProgram);
  program.required = true;
  const ref = input("text");
  const title = input("text", record?.title, { required: true });
  const applicant = input("text", record?.applicant, { required: true });
  const email = input("text", record?.email, { inputMode: "email", autocomplete: "email" });
  const team = input("textarea", (record?.team || []).join("\n"));
  const files = input("file", "", { multiple: true });
  const actor = input("text", "", { required: true });
  const removeFiles = [];
  const options = el("div", "form-grid");
  let dynamic;
  function loadRules(id) {
    dynamic = applicationFieldControls(programFor(id), record?.fields || {}, email);
    options.hidden = !dynamic.items.length;
    options.replaceChildren(...dynamic.items);
  }
  loadRules(initialProgram);
  program.addEventListener("change", () => loadRules(program.value));
  const core = el("div", "form-grid", amending ? null : field("資助計劃", program, "", true),
    amending ? null : field("申請編號（選填）", ref, "留空會自動編配申請編號。", true),
    field("項目名稱", title), field("申請機構／人", applicant), field("聯絡電郵", email),
    field("團隊成員", team, "每行輸入一名成員。", true),
    amending ? field("修訂人", actor) : null);
  let fileList;
  if (amending && record.documents?.length) {
    fileList = el("fieldset", "form-group", el("legend", "", "現有附件"));
    for (const file of record.documents) {
      const toggle = input("checkbox");
      removeFiles.push([file.name, toggle]);
      fileList.append(el("label", "check-choice", toggle, el("span", "", `移除 ${file.name}`)));
    }
  }
  const instructions = amending
    ? el("p", "hint", "修訂後系統會重新核查，原有評分及人工覆核確認會清除。")
    : el("p", "hint", "資料未齊也可登記，收件後可查看待補交項目。");
  return saveForm(amending ? "補件或修訂申請" : "申請資料", [instructions, core, options, fileList,
    field(amending ? "新附件或替換檔案" : "上載附件", files,
      amending ? "上載同名檔案可替換現有附件。" : "檔案可一次選取多份。")], {
    inCase: amending, label: amending ? "儲存修訂" : "登記申請", success: amending ? "已儲存修訂並重新核查。" : "已登記申請。",
    submit: () => {
      const metadata = {
        program_id: amending ? record.program_id : program.value,
        title: title.value.trim(), applicant: applicant.value.trim(), email: email.value.trim(),
        team: stringList(team.value), fields: { ...record?.fields, ...dynamic.read() },
      };
      if (!amending && ref.value.trim()) metadata.app_id = ref.value.trim();
      const body = new FormData();
      body.append("metadata", JSON.stringify(metadata));
      for (const file of files.files) body.append("files", file);
      if (amending) {
        body.append("revision", record.revision);
        body.append("actor", actor.value.trim());
        for (const [name, toggle] of removeFiles) if (toggle.checked) body.append("removed_files", name);
      }
      return api(amending ? `${casePath(record.id)}/materials` : "/api/cases", { method: amending ? "PUT" : "POST", body });
    },
  });
}

function renderNew() {
  if (state.programs.length) show(viewTitle("新增申請"), applicationForm());
  else show(viewTitle("新增申請"), el("p", "muted", "未載入任何資助計劃。請檢查計劃配置。"));
}

function renderMail() {
  const message = input("file", "", { required: true, accept: ".eml,message/rfc822" });
  const form = saveForm("電郵檔案", [field("選擇 .eml 檔案", message, "請從郵件程式儲存原始電郵為 .eml 檔案，匯入時會一併處理附件。")], {
    label: "匯入個案", success: "已匯入電郵個案。",
    submit: () => {
      const body = new FormData();
      body.append("message", message.files[0]);
      return api("/api/mail", { method: "POST", body });
    },
  });
  show(viewTitle("匯入電郵"), form);
}

function repeatFields(title, addLabel, items, build) {
  const parent = el("fieldset", "form-group", el("legend", "", title));
  const entries = [];
  const rows = el("div", "stack");
  const empty = el("p", "hint", "未有記錄，可按下方按鈕新增。");
  function update() {
    empty.hidden = entries.length > 0;
    entries.forEach((entry, index) => {
      entry.legend.textContent = `${title} ${index + 1}`;
      entry.remove.setAttribute("aria-label", `移除${title} ${index + 1}`);
    });
  }
  function add(value, focus = false) {
    const entry = build(value);
    entry.legend = el("strong", "");
    entry.remove = button("移除", () => {
      entries.splice(entries.indexOf(entry), 1);
      entry.row.remove();
      update();
      addButton.focus();
    }, "quiet");
    entry.row = el("div", "form-row", el("div", "form-row-options", entry.legend, entry.remove), el("div", "form-grid", ...entry.fields));
    entries.push(entry);
    rows.append(entry.row);
    update();
    if (focus) entry.row.querySelector("input, select, textarea")?.focus();
  }
  const addButton = button(addLabel, () => add({}, true), "secondary", "plus");
  parent.append(empty, rows, addButton);
  items.forEach((value) => add(value));
  update();
  return { parent, read: () => entries.map((entry) => entry.read()) };
}

function scoresForm(record, program) {
  const criteria = program?.criteria || [];
  const initial = record.scores?.length ? record.scores : [{}];
  const scores = repeatFields("評審", "新增評審", initial, (entry) => {
    const reviewer = input("text", entry.reviewer, { required: true });
    const comment = input("textarea", entry.comment);
    const fields = criteria.map((criterion) => [criterion, input("number", entry.criteria?.[criterion.name], {
      required: true, min: 0, max: criterion.max_score, step: "any",
    })]);
    return {
      fields: [field("評審人", reviewer, "", true),
        ...fields.map(([criterion, control]) => field(`${criterion.name}（滿分 ${criterion.max_score}）`, control)),
        field("評審意見（選填）", comment, "", true)],
      read: () => ({ reviewer: reviewer.value.trim(), comment: comment.value.trim(),
        criteria: Object.fromEntries(fields.map(([criterion, control]) => [criterion.name, control.valueAsNumber])) }),
    };
  });
  if (!criteria.length) return panel("登記評分", el("p", "muted", "未有此計劃的評分準則。請檢查計劃配置。"), button("返回", closeEditor));
  return saveForm("登記評分", [el("p", "hint", "此表單儲存整組評分，包括評審名單及評語。"), scores.parent], {
    inCase: true, label: "儲存評分", success: "已儲存並重新匯總評分。",
    submit: () => jsonRequest(`${casePath(record.id)}/scores`, "PUT", { revision: record.revision, scores: scores.read() }),
  });
}

function resolutionForm(record) {
  const actor = input("text", "", { required: true });
  const reason = input("textarea", "", { required: true });
  return saveForm("登記人工覆核", [field("覆核人", actor), field("覆核結果及理由", reason,
    "記下已覆核的發現、確認結果及理據。")], {
    inCase: true, label: "確認覆核", success: "已登記人工覆核。",
    submit: () => jsonRequest(`${casePath(record.id)}/resolution`, "POST", { revision: record.revision, actor: actor.value.trim(), reason: reason.value.trim() }),
  });
}

function decisionForm(record) {
  const assessment = assessmentFor(record);
  const actor = input("text", "", { required: true });
  const reason = input("textarea", "", { required: true });
  const approved = input("radio", "approved", { name: "outcome", required: true, disabled: !assessment.approval_allowed });
  const declined = input("radio", "declined", { name: "outcome", required: true, disabled: !assessment.decline_allowed });
  const choices = el("fieldset", "form-group", el("legend", "", "批核結果"),
    el("label", "radio-choice", approved, "批核申請"), el("label", "radio-choice", declined, "拒絕申請"),
    !assessment.approval_allowed ? el("p", "hint", "待處理事項完成後才可批核申請。") : null);
  return saveForm("作出批核", [choices, field("批核人", actor), field("批核理由", reason),
    el("p", "hint", "確認後會鎖定個案的申請資料及評分，並產生結果通知稿。")], {
    inCase: true, label: "確認批核結果", success: "已登記批核結果。",
    submit: () => jsonRequest(`${casePath(record.id)}/decision`, "POST", {
      revision: record.revision, actor: actor.value.trim(), reason: reason.value.trim(), outcome: approved.checked ? approved.value : declined.value,
    }),
  });
}

function dateField(value = "", required = false) {
  return input("date", value ? value.slice(0, 10) : "", { required });
}

function projectForm(record) {
  const data = record.project || {};
  const actor = input("text", "", { required: true });
  const milestoneEditor = repeatFields("里程碑", "新增里程碑", data.milestones || [], (row) => {
    const name = input("text", row.name, { required: true });
    const due = dateField(row.due, true);
    const status = selectControl([["pending", "待完成"], ["done", "已完成"]], row.status === "done" ? "done" : "pending");
    return {
      fields: [field("里程碑名稱", name, "", true), field("到期日", due), field("進度", status)],
      read: () => ({ name: name.value.trim(), due: due.value, status: status.value }),
    };
  });
  const disbursementEditor = repeatFields("資助發放", "新增發放記錄", data.disbursements || [], (row) => {
    const name = input("text", row.name, { required: true });
    const due = dateField(row.due, true);
    const amount = input("number", row.amount, { required: true, min: 0, step: "0.01" });
    const paid = input("number", row.paid_amount ?? 0, { min: 0, step: "0.01" });
    const paidAt = dateField(row.paid_at);
    return {
      fields: [field("款項名稱", name, "", true), field("發放期限", due), field("計劃發放額（HKD）", amount),
        field("已發放額（HKD）", paid), field("發放日期（選填）", paidAt)],
      read: () => ({ name: name.value.trim(), due: due.value, amount: amount.valueAsNumber, paid_amount: Number(paid.value), paid_at: paidAt.value }),
    };
  });
  const reportEditor = repeatFields("定期報告", "新增報告", data.periodic_reports || [], (row) => {
    const name = input("text", row.name, { required: true });
    const due = dateField(row.due, true);
    const submitted = dateField(row.submitted_at);
    return {
      fields: [field("報告名稱", name, "", true), field("到期日", due), field("提交日期（選填）", submitted)],
      read: () => ({ name: name.value.trim(), due: due.value, submitted_at: submitted.value }),
    };
  });
  return saveForm("更新項目進度", [field("更新人", actor), milestoneEditor.parent, disbursementEditor.parent, reportEditor.parent], {
    inCase: true, label: "儲存項目進度", success: "已儲存項目進度。",
    submit: () => jsonRequest(`${casePath(record.id)}/project`, "PUT", {
      revision: record.revision, actor: actor.value.trim(), milestones: milestoneEditor.read(),
      disbursements: disbursementEditor.read(), periodic_reports: reportEditor.read(),
    }),
  });
}

function navigate(focus = true) {
  const route = readRoute();
  state.route = route;
  state.caseRequest++;
  state.activeCase = null;
  state.tab = "overview";
  state.editor = null;
  if (state.keepNotice) state.keepNotice = false;
  else notices.replaceChildren();
  if (route.view === "list" && route.queue !== state.queue) {
    state.queue = route.queue;
    state.statusFilter = "";
  }
  renderRail();
  renderBreadcrumbs();
  if (focus) window.scrollTo(0, 0);
  if (route.view === "case") {
    loadCase(route.id, focus);
    return;
  }
  if (route.view === "mail") renderMail();
  else if (!state.listReady) {
    loading(route.view === "new" ? "新增申請" : "申請個案", "正在載入資助計劃和個案…");
    if (!state.registryLoading) reloadRegistry();
  } else if (route.view === "new") renderNew();
  else renderRegister();
  if (focus) content.focus();
}

for (const node of document.querySelectorAll("[data-icon]")) node.append(icon(node.dataset.icon));
document.querySelector(".skip-link").addEventListener("click", (event) => { event.preventDefault(); content.focus(); });
demoButton.addEventListener("click", importDemo);
window.addEventListener("hashchange", () => navigate());
navigate(false);
if (!state.registryLoading) reloadRegistry();
