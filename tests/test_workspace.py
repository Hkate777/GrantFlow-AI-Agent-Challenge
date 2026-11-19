import json
from email.message import EmailMessage

from fastapi.testclient import TestClient

import app


def _client(path):
    return TestClient(app.create_app(path), client=("127.0.0.1", 41000))


def _metadata(app_id="PORTAL-1", program="HK-ACC-2027"):
    return {"app_id": app_id, "program_id": program, "title": "Fixture project", "applicant": "Fixture team",
            "email": "test@example.test", "team": ["Team lead"],
            "fields": {"org_type": "university", "contact": "Team lead", "email": "test@example.test",
                       "budget": 120000, "duration_m": 6}}


def _files(include_proposal=True):
    documents = [("files", ("budget.csv", b"item,amount\nMaterials,120000\n", "text/csv")),
                 ("files", ("cv.txt", b"Team lead biography.", "text/plain"))]
    if include_proposal:
        documents.append(("files", ("proposal.txt", b"requested_budget: 120000 HKD", "text/plain")))
    return documents


def _upload(client, metadata=None, files=None):
    return client.post("/api/cases", data={"metadata": json.dumps(metadata or _metadata())},
                       files=_files() if files is None else files)


def _save_scores(client, case):
    programs = client.get("/api/programs").json()
    criteria = next(program["criteria"] for program in programs if case["program_id"] == program["program_id"])
    # 計劃要求 min_reviewers（預設 2）：兩位評審一致滿分，讓簽發路徑可達。
    return client.put(f"/api/cases/{case['id']}/scores", json={"revision": case["revision"], "scores": [
        {"reviewer": f"Reviewer {letter}", "criteria": {item["name"]: item["max_score"] for item in criteria},
         "comment": "Feasible."} for letter in ("A", "B")],
    })


def test_portal_replay_does_not_duplicate_or_replace_case(tmp_path):
    with _client(tmp_path / "workspace.db") as client:
        first = _upload(client)
        assert first.status_code == 201, first.text
        case = first.json()
        replay = _upload(client, files=list(reversed(_files())))
        assert (replay.json()["id"], replay.json()["revision"]) == (case["id"], 1)
        changed = _upload(client, {**_metadata(), "title": "Different submission"})
        assert changed.status_code == 409
        assert len(client.get("/api/cases").json()) == 1
        assert client.get(f"/api/cases/{case['id']}/pack").content.startswith(b"PK")


def test_email_routes_to_an_independent_plan(tmp_path):
    metadata = _metadata("MAIL-1", "HK-CAMPUS-2027")
    mail = EmailMessage()
    mail["Subject"] = "Fixture application"
    mail.set_content("Application attached.")
    mail.add_attachment(json.dumps(metadata).encode(), maintype="application", subtype="json",
                        filename="application.json")
    mail.add_attachment(b"Other text format", maintype="text", subtype="plain", filename="proposal.txt")
    with _client(tmp_path / "workspace.db") as client:
        response = client.post("/api/mail", files={"message": ("application.eml", mail.as_bytes(), "message/rfc822")})
        assert response.status_code == 201, response.text
        case = response.json()
        assert case["channel"] == "email"
        assert case["program_id"] == "HK-CAMPUS-2027"
        assert case["rule_version"]
        assert any(row["source"] == "required_docs" and row["finding_type"] == "format" for row in case["findings"])


def test_supplement_invalidates_scores_and_stale_finals_fail(tmp_path):
    with _client(tmp_path / "workspace.db") as client:
        case = _upload(client, files=_files(False)).json()
        assert case["assessment"]["status"] == "needs_materials"
        scored = _save_scores(client, case).json()
        route = f"/api/cases/{case['id']}"
        early = client.post(route + "/decision", json={"revision": scored["revision"], "actor": "Officer",
                                                     "reason": "Inspected", "outcome": "approved"})
        assert early.status_code == 409
        added = client.put(route + "/materials", data={"revision": scored["revision"], "actor": "Officer",
                                                       "metadata": json.dumps(_metadata())}, files=_files()[-1:])
        assert added.status_code == 200, added.text
        new_case = added.json()
        assert new_case["scores"] == []
        assert new_case["assessment"]["status"] == "pending_scores"
        assert _save_scores(client, scored).status_code == 409


def test_manual_approval_and_project_survive_restart(tmp_path):
    database = tmp_path / "workspace.db"
    with _client(database) as client:
        case = _upload(client).json()
        scored = _save_scores(client, case).json()
        assert scored["assessment"]["status"] == "awaiting_decision"
        assert scored["assessment"]["notice"] is None
        url = f"/api/cases/{case['id']}"
        signed = client.post(url + "/decision", json={"revision": scored["revision"], "outcome": "approved",
                                                       "actor": "Officer", "reason": "Checked all source records"})
        assert signed.status_code == 200, signed.text
        final = signed.json()
        assert final["assessment"]["status"] == "approved"
        assert final["assessment"]["notice"]
        update = client.put(url + "/project", json={"revision": final["revision"], "actor": "Officer",
            "milestones": [{"name": "Review", "due": "2020-01-01", "status": "done"}],
            "disbursements": [{"name": "First grant", "due": "2020-01-01", "amount": 5000,
                               "paid_amount": 1500, "paid_at": "2020-02-01"}],
            "periodic_reports": [{"name": "Monthly report", "due": "2020-03-01", "submitted_at": ""}]})
        assert update.status_code == 200, update.text

    with _client(database) as client:
        saved = client.get(url).json()
        assert saved["project"]["funding"] == {"scheduled": 5000, "paid": 1500}
        assert any("Monthly report" in warning for warning in saved["project"]["alerts"])
        assert saved["audit"][-1]["actor"] == "Officer"
        assert client.get(url + "/report").status_code == 200


def test_mutations_require_same_browser_origin(tmp_path):
    with _client(tmp_path / "workspace.db") as client:
        response = client.post("/api/demo", headers={"Origin": "https://another-site.example"})
        assert response.status_code == 403
        assert client.get("/api/cases").json() == []
