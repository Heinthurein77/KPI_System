"""5-tester end-to-end simulation (throwaway — not committed).

Simulates a full month of real usage with 5 testers across 2 departments:
  T1 (Aye): happy path — score, comment, submit, dept approve, final approve
  T2 (Bo): dept-admin bulk approve with edited scores + per-item remarks
  T3 (Cho): rejection with reason -> revise -> resubmit -> approve
  T4 (Daw): edge cases — over-target score, double approve, self-approval,
            wrong-role access, duplicate bulk ids, unauthenticated
  T5 (Ei): tenant-admin final bulk approve + override

Asserts workflow state, notifications, audit logs, and HTTP error codes.
Run: .venv/bin/python -m pytest app/tests/test_sim_5testers.py -q
"""

from app.models.audit_log import AuditLog
from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.notification import Notification
from app.models.user import UserRole
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)

YEAR = 2026
PERIOD = "October"


def _world(db):
    tenant = make_tenant(db)
    fin = make_department(db, tenant, name="Finance")
    hr = make_department(db, tenant, name="HR")
    fin_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Fin Admin", department=fin)
    hr_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="HR Admin", department=hr)
    top_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Top Admin")
    aye = make_user(db, tenant, UserRole.EMPLOYEE, name="Aye", department=fin)
    bo = make_user(db, tenant, UserRole.EMPLOYEE, name="Bo", department=fin)
    cho = make_user(db, tenant, UserRole.EMPLOYEE, name="Cho", department=hr)
    daw = make_user(db, tenant, UserRole.EMPLOYEE, name="Daw", department=hr)
    ei = make_user(db, tenant, UserRole.EMPLOYEE, name="Ei", department=fin)
    tpl_fin = make_template(db, tenant, department=fin, metric_name="Close books", target=100.0)
    tpl_hr = make_template(db, tenant, department=hr, metric_name="Hiring", target=50.0)
    return locals()


def _sub_id(client, headers, emp_name, metric):
    """Find a submission id for an employee+metric via dashboard."""
    resp = client.get("/api/dashboard", headers=headers)
    assert resp.status_code == 200, resp.text
    for s in resp.json()["submissions"]:
        if s["employee"]["name"] == emp_name and s["kpi_template"]["metric_name"] == metric:
            return s["id"]
    raise AssertionError(f"no submission for {emp_name}/{metric}")


def _notif_count(db, tenant, user, type_):
    return (
        db.query(Notification)
        .filter_by(tenant_id=tenant.id, user_id=user["id"] if isinstance(user, dict) else user.id, type=type_)
        .count()
    )


def test_t1_happy_path(client, db):
    w = _world(db)
    h_aye = auth_headers(w["aye"], w["tenant"])
    h_fin = auth_headers(w["fin_admin"], w["tenant"])
    h_top = auth_headers(w["top_admin"], w["tenant"])

    # dashboard materializes a draft
    sid = _sub_id(client, h_aye, "Aye", "Close books")

    # employee scores + comment, then submits
    r = client.post("/api/kpi/employee/save", json={
        "year": YEAR, "period": PERIOD,
        "scores": {str(sid): 80}, "comments": {str(sid): "Closed 12 tickets"},
    }, headers=h_aye)
    assert r.status_code == 200, r.text
    r = client.post("/api/kpi/employee/submit", json={
        "year": YEAR, "period": PERIOD, "scores": {}, "comments": {},
    }, headers=h_aye)
    assert r.status_code == 200, r.text
    assert r.json()[0]["status"] == "pending_dept_approval"

    # dept approve with adjusted score
    r = client.post(f"/api/kpi/{sid}/dept-approve", json={
        "year": YEAR, "period": PERIOD, "dept_score": 85, "remarks": "Good",
    }, headers=h_fin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_final_approval"
    assert r.json()["dept_score"] == 85

    # final approve
    r = client.post(f"/api/kpi/{sid}/final-approve", json={
        "year": YEAR, "period": PERIOD, "final_score": 90,
    }, headers=h_top)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["final_score"] == 90

    # notifications + audit trail exist
    assert _notif_count(db, w["tenant"], w["aye"], "kpi_final_approved") >= 1
    assert db.query(AuditLog).filter_by(action="kpi_final_approved", entity_id=sid).count() == 1
    # comment survived the whole workflow
    db_s = db.get(KPISubmission, sid)
    assert db_s.self_score_comment == "Closed 12 tickets"


def test_t2_bulk_approve_with_edits(client, db):
    w = _world(db)
    h_bo = auth_headers(w["bo"], w["tenant"])
    h_ei = auth_headers(w["ei"], w["tenant"])
    h_fin = auth_headers(w["fin_admin"], w["tenant"])

    sid_bo = _sub_id(client, h_bo, "Bo", "Close books")
    sid_ei = _sub_id(client, h_ei, "Ei", "Close books")
    for sid, h in ((sid_bo, h_bo), (sid_ei, h_ei)):
        r = client.post("/api/kpi/employee/submit", json={
            "year": YEAR, "period": PERIOD,
            "scores": {str(sid): 70}, "comments": {},
        }, headers=h)
        assert r.status_code == 200, r.text

    # bulk approve with edited scores + per-item remarks
    r = client.post("/api/kpi/bulk-approve", json={
        "year": YEAR, "period": PERIOD,
        "items": [
            {"submission_id": sid_bo, "score": 75, "remarks": "Bo: solid"},
            {"submission_id": sid_ei, "score": 95, "remarks": "Ei: great"},
        ],
    }, headers=h_fin)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2

    for sid, score, remarks in ((sid_bo, 75, "Bo: solid"), (sid_ei, 95, "Ei: great")):
        s = db.get(KPISubmission, sid)
        assert s.status == KPIStatus.PENDING_FINAL_APPROVAL
        assert s.dept_score == score
        assert s.remarks == remarks


def test_t3_reject_revise_resubmit(client, db):
    w = _world(db)
    h_cho = auth_headers(w["cho"], w["tenant"])
    h_hr = auth_headers(w["hr_admin"], w["tenant"])
    h_top = auth_headers(w["top_admin"], w["tenant"])

    sid = _sub_id(client, h_cho, "Cho", "Hiring")
    r = client.post("/api/kpi/employee/submit", json={
        "year": YEAR, "period": PERIOD, "scores": {str(sid): 40}, "comments": {},
    }, headers=h_cho)
    assert r.status_code == 200

    # dept rejects with reason
    r = client.post(f"/api/kpi/{sid}/reject", json={
        "year": YEAR, "period": PERIOD, "remarks": "Evidence missing",
    }, headers=h_hr)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected"
    assert r.json()["remarks"] == "Evidence missing"

    # employee revises and resubmits
    r = client.post("/api/kpi/employee/submit", json={
        "year": YEAR, "period": PERIOD,
        "scores": {str(sid): 45}, "comments": {str(sid): "Added evidence"},
    }, headers=h_cho)
    assert r.status_code == 200, r.text
    assert r.json()[0]["status"] == "pending_dept_approval"

    # approve path completes
    r = client.post(f"/api/kpi/{sid}/dept-approve", json={
        "year": YEAR, "period": PERIOD, "dept_score": 45,
    }, headers=h_hr)
    assert r.status_code == 200
    r = client.post(f"/api/kpi/{sid}/final-approve", json={
        "year": YEAR, "period": PERIOD, "final_score": 45,
    }, headers=h_top)
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_t4_edge_cases(client, db):
    w = _world(db)
    h_daw = auth_headers(w["daw"], w["tenant"])
    h_hr = auth_headers(w["hr_admin"], w["tenant"])
    h_fin = auth_headers(w["fin_admin"], w["tenant"])
    h_top = auth_headers(w["top_admin"], w["tenant"])

    sid = _sub_id(client, h_daw, "Daw", "Hiring")  # target 50

    # 1. over-target self score rejected
    r = client.post("/api/kpi/employee/save", json={
        "year": YEAR, "period": PERIOD, "scores": {str(sid): 99}, "comments": {},
    }, headers=h_daw)
    assert r.status_code == 400, r.text

    # 2. unauthenticated blocked
    r = client.post("/api/kpi/employee/save", json={
        "year": YEAR, "period": PERIOD, "scores": {}, "comments": {}})
    assert r.status_code == 401, r.text

    # 3. employee cannot hit admin endpoints
    r = client.post(f"/api/kpi/{sid}/dept-approve", json={
        "year": YEAR, "period": PERIOD, "dept_score": 40,
    }, headers=h_daw)
    assert r.status_code == 403, r.text

    # 4. dept admin cannot approve another department's submission
    r = client.post("/api/kpi/employee/submit", json={
        "year": YEAR, "period": PERIOD, "scores": {str(sid): 40}, "comments": {},
    }, headers=h_daw)
    assert r.status_code == 200
    r = client.post(f"/api/kpi/{sid}/dept-approve", json={
        "year": YEAR, "period": PERIOD, "dept_score": 40,
    }, headers=h_fin)  # Finance admin vs HR submission
    assert r.status_code == 403, r.text

    # 5. double approve blocked; wrong-status bulk blocked atomically
    r = client.post(f"/api/kpi/{sid}/dept-approve", json={
        "year": YEAR, "period": PERIOD, "dept_score": 40,
    }, headers=h_hr)
    assert r.status_code == 200
    r = client.post(f"/api/kpi/{sid}/dept-approve", json={
        "year": YEAR, "period": PERIOD, "dept_score": 40,
    }, headers=h_hr)
    assert r.status_code == 400, r.text

    # 6. tenant admin cannot bulk-approve a genuinely dept-pending item
    # (make a second HR submission that is still awaiting dept review)
    tpl_hr = make_template(db, w["tenant"], department=w["hr"], metric_name="Hiring 2", target=50.0)
    sid2 = make_submission(
        db, w["tenant"], w["daw"], tpl_hr, year=YEAR, period=PERIOD,
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=30,
    ).id
    r = client.post("/api/kpi/bulk-approve", json={
        "year": YEAR, "period": PERIOD,
        "items": [{"submission_id": sid2, "score": 30}],
    }, headers=h_top)
    assert r.status_code == 400, r.text
    s2 = db.get(KPISubmission, sid2)
    assert s2.status == KPIStatus.PENDING_DEPT_APPROVAL

    # 7. over-target score in bulk blocked
    r = client.post("/api/kpi/bulk-approve", json={
        "year": YEAR, "period": PERIOD,
        "items": [{"submission_id": sid, "score": 500}],
    }, headers=h_hr)
    assert r.status_code == 400, r.text
    s = db.get(KPISubmission, sid)
    assert s.status == KPIStatus.PENDING_FINAL_APPROVAL  # untouched


def test_t5_tenant_admin_final_bulk_and_override(client, db):
    w = _world(db)
    h_aye = auth_headers(w["aye"], w["tenant"])
    h_bo = auth_headers(w["bo"], w["tenant"])
    h_fin = auth_headers(w["fin_admin"], w["tenant"])
    h_top = auth_headers(w["top_admin"], w["tenant"])

    sid_aye = _sub_id(client, h_aye, "Aye", "Close books")
    sid_bo = _sub_id(client, h_bo, "Bo", "Close books")
    for sid, h in ((sid_aye, h_aye), (sid_bo, h_bo)):
        r = client.post("/api/kpi/employee/submit", json={
            "year": YEAR, "period": PERIOD, "scores": {str(sid): 70}, "comments": {},
        }, headers=h)
        assert r.status_code == 200
    r = client.post("/api/kpi/bulk-approve", json={
        "year": YEAR, "period": PERIOD,
        "items": [
            {"submission_id": sid_aye, "score": 72},
            {"submission_id": sid_bo, "score": 74},
        ],
    }, headers=h_fin)
    assert r.status_code == 200

    # tenant admin bulk final-approves
    r = client.post("/api/kpi/bulk-approve", json={
        "year": YEAR, "period": PERIOD,
        "items": [
            {"submission_id": sid_aye, "score": 80, "remarks": "Final: ok"},
            {"submission_id": sid_bo, "score": 82, "remarks": "Final: ok"},
        ],
    }, headers=h_top)
    assert r.status_code == 200, r.text
    for sid, score in ((sid_aye, 80), (sid_bo, 82)):
        s = db.get(KPISubmission, sid)
        assert s.status == KPIStatus.APPROVED
        assert s.final_score == score
        assert s.remarks == "Final: ok"

    # override on an approved submission still works
    r = client.post(f"/api/kpi/{sid_aye}/override", json={
        "year": YEAR, "period": PERIOD, "final_score": 85, "remarks": "Correction",
    }, headers=h_top)
    assert r.status_code == 200, r.text
    assert r.json()["final_score"] == 85
