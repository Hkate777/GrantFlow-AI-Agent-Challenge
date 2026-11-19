"""GrantFlow Web面板：FastAPI提供最小可用UI —— 上傳批次、查看發現、評審彙總、追蹤預警。

運行：python app.py至http://127.0.0.1:8000
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
from kkg.region import Region                          # noqa: E402

BASE = Path(__file__).parent
prog = Program.load(BASE / "configs" / "program_demo.yaml")
regions = Region.load_all(BASE / "configs" / "regions")
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
<h1>GrantFlow · 申請評審與項目管理流程自動化 <span class="badge">KK_Garden · 場景16</span></h1>
<p>計劃：<b id="pname"></b>（截止 <span id="pdl"></span>）</p>
<label for="region">審查地區：</label><select id="region"></select> <button onclick="run()">運行Demo批次審查</button>
<div id="out"></div>
<script>
fetch('/api/program').then(r=>r.json()).then(p=>{pname.textContent=p.name;pdl.textContent=p.deadline||'N/A'});
fetch('/api/regions').then(r=>r.json()).then(rs=>rs.forEach(r=>{let o=document.createElement('option');o.value=r.code;o.textContent=r.name;region.appendChild(o)}));
function run(){
 out.innerHTML='<p>運行中…</p>';
 fetch('/api/run?region='+encodeURIComponent(region.value)).then(r=>r.json()).then(d=>{
  let h='<h2>審查結果</h2>';
  for(const a of d.applications){
   h+=`<div class="card"><b>${a.app_id} «${a.title}»</b> — ${a.applicant}<br>發現 ${a.findings.length}項：`;
   if(!a.findings.length)h+=' [通過] 無問題';
   h+='<ul>'+a.findings.map(f=>`<li class="sev-${f.severity}">[${f.type}] ${f.message}${f.needs_human?'（需人工複核）':''}</li>`).join('')+'</ul></div>';
  }
  if(d.batch_findings.length){h+='<h2>批次檢查</h2><ul>'+d.batch_findings.map(f=>`<li class="sev-${f.severity}">${f.message}</li>`).join('')+'</ul>';}
  h+='<h2>審計日誌</h2><pre>'+JSON.stringify(d.audit,null,2)+'</pre>';
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


@app.get("/api/regions")
def api_regions() -> list[dict]:
    return [{"code": r.code, "name": r.name} for r in regions.values()]


@app.get("/api/run")
def api_run(region: str = "HK") -> JSONResponse:
    active_region = regions.get(region.upper(), regions["HK"])
    local_pipe = Pipeline(prog, llm=get_llm("auto"), region=active_region)
    apps = load_applications(BASE / "data" / "demo" / "applications")
    result_apps = []
    for a in apps:
        _, findings = local_pipe.intake(a)
        result_apps.append({
            "app_id": a.app_id, "title": a.title, "applicant": a.applicant,
            "findings": [{"type": f.finding_type.value, "severity": f.severity.value,
                          "message": f.message, "needs_human": f.needs_human} for f in findings],
        })
    batch = local_pipe.batch_checks(apps)
    return JSONResponse({
        "applications": result_apps,
        "batch_findings": [{"severity": f.severity.value, "message": f.message} for f in batch],
        "audit": json.loads(local_pipe.audit_json()),
    })
