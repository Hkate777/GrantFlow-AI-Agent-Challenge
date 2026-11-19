"""GrantFlow Web 面板：FastAPI 提供最小可用 UI —— 上传批次、查看发现、评审汇总、追踪预警。

运行：python app.py  →  http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastapi import FastAPI                            # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from kkg.llm import get_llm                            # noqa: E402
from kkg.pipeline import Pipeline, load_applications   # noqa: E402
from kkg.program import Program                        # noqa: E402

BASE = Path(__file__).parent
prog = Program.load(BASE / "configs" / "program_demo.yaml")
pipe = Pipeline(prog, llm=get_llm("auto"))

app = FastAPI(title="GrantFlow · KK_Garden", version="0.1.0")

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>GrantFlow · KK_Garden</title>
<style>
body{font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1f2937}
h1{font-size:1.4rem} .card{border:1px solid #e5e7eb;border-radius:10px;padding:1rem;margin:.8rem 0}
.sev-error,.sev-critical{color:#dc2626}.sev-warning{color:#d97706}.sev-info{color:#2563eb}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #e5e7eb;padding:.4rem .6rem;text-align:left}
.badge{display:inline-block;background:#eef2ff;border-radius:999px;padding:.1rem .6rem;font-size:.8rem}
button{background:#4f46e5;color:#fff;border:0;border-radius:8px;padding:.5rem 1.1rem;cursor:pointer}
pre{background:#f9fafb;padding:.8rem;border-radius:8px;overflow:auto}
</style></head><body>
<h1>GrantFlow · 申请评审与项目管理流程自动化 <span class="badge">KK_Garden · 场景16</span></h1>
<p>计划：<b id="pname"></b>（截止 <span id="pdl"></span>）</p>
<button onclick="run()">▶ 运行 Demo 批次审查</button>
<div id="out"></div>
<script>
fetch('/api/program').then(r=>r.json()).then(p=>{pname.textContent=p.name;pdl.textContent=p.deadline||'N/A'});
function run(){
 out.innerHTML='<p>运行中…</p>';
 fetch('/api/run').then(r=>r.json()).then(d=>{
  let h='<h2>审查结果</h2>';
  for(const a of d.applications){
   h+=`<div class="card"><b>${a.app_id} «${a.title}»</b> — ${a.applicant}<br>发现 ${a.findings.length} 项：`;
   if(!a.findings.length)h+=' ✅ 无问题';
   h+='<ul>'+a.findings.map(f=>`<li class="sev-${f.severity}">[${f.type}] ${f.message}${f.needs_human?'（需人工复核）':''}</li>`).join('')+'</ul></div>';
  }
  if(d.batch_findings.length){h+='<h2>批次检查</h2><ul>'+d.batch_findings.map(f=>`<li class="sev-${f.severity}">${f.message}</li>`).join('')+'</ul>';}
  h+='<h2>审计日志</h2><pre>'+JSON.stringify(d.audit,null,2)+'</pre>';
  out.innerHTML=h;
 });
}
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/api/program")
def api_program() -> dict:
    return {"program_id": prog.program_id, "name": prog.name, "deadline": prog.deadline,
            "criteria": [vars(c) for c in prog.criteria]}


@app.get("/api/run")
def api_run() -> JSONResponse:
    apps = load_applications(BASE / "data" / "demo" / "applications")
    result_apps = []
    for a in apps:
        _, findings = pipe.intake(a)
        result_apps.append({
            "app_id": a.app_id, "title": a.title, "applicant": a.applicant,
            "findings": [{"type": f.finding_type.value, "severity": f.severity.value,
                          "message": f.message, "needs_human": f.needs_human} for f in findings],
        })
    batch = pipe.batch_checks(apps)
    return JSONResponse({
        "applications": result_apps,
        "batch_findings": [{"severity": f.severity.value, "message": f.message} for f in batch],
        "audit": json.loads(pipe.audit_json()),
    })
