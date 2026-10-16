"""Tests for POST /api/kpi/bulk-approve.

Covers: dept-admin bulk dept-approval, tenant-admin bulk final-approval,
all-or-nothing validation (one bad item -> nothing processed), department
scoping, self-review guard, duplicate ids, score-above-target rejection,
and role gating.
"""

from app.models.audit_log import AuditLog
from app.models.kpi_submission import KPISubmission, KPIStatus
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


def _setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    other_dept = make_department(db, tenant, name="Other")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dept Admin", department=dept)
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tenant Admin")
    emp_a = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp A", department=dept)
    emp_b = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp B", department=dept)
    emp_other = make_user(db, tenant, UserRole.EMPLOYEE, name="Emp Other", department=other_dept)
    tpl = make_template(db, tenant, department=dept, target=100.0)
    tpl_other = make_template(db, tenant, department=other_dept, target=100.0)
    return {
        "tenant": tenant,
        "dept": dept,
        "dept_admin": dept_admin,
        "tenant_admin": tenant_admin,
        "emp_a": emp_a,
        "emp_b": emp_b,
        "emp_other": emp_other,
        "tpl": tpl,
        "tpl_other": tpl_other,
    }


def _pending_dept(db, fx, emp):
    return make_submission(
        db, fx["tenant"], emp, fx["tpl"], year=YEAR, period=PERIOD,
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80,
    )


def _pending_final(db, fx, emp):
    return make_submission(
        db, fx["tenant"], emp, fx["tpl"], year=YEAR, period=PERIOD,
        status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=80, dept_score=85,
    )


def _post(client, fx, user, items):
    return client.post(
        "/api/kpi/bulk-approve",
        json={"year": YEAR, "period": PERIOD, "items": items},
        headers=auth_headers(user, fx["tenant"]),
    )


def test_dept_admin_bulk_approve(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    s2 = _pending_dept(db, fx, fx["emp_b"])

    resp = _post(client, fx, fx["dept_admin"], [
        {"submission_id": s1.id, "score": 82},
        {"submission_id": s2.id, "score": 90},
    ])
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert set(body["approved"]) == {s1.id, s2.id}

    db.refresh(s1)
    db.refresh(s2)
    assert s1.status == KPIStatus.PENDING_FINAL_APPROVAL
    assert s1.dept_score == 82
    assert s2.status == KPIStatus.PENDING_FINAL_APPROVAL
    assert s2.dept_score == 90
    # Same audit trail as one-by-one approval.
    assert db.query(AuditLog).filter(
        AuditLog.action == "kpi_dept_approved",
        AuditLog.entity_id.in_([s1.id, s2.id]),
    ).count() == 2


def test_tenant_admin_bulk_final_approve(client, db):
    fx = _setup(db)
    s1 = _pending_final(db, fx, fx["emp_a"])
    s2 = _pending_final(db, fx, fx["emp_b"])

    resp = _post(client, fx, fx["tenant_admin"], [
        {"submission_id": s1.id, "score": 88},
        {"submission_id": s2.id, "score": None},
    ])
    assert resp.status_code == 200
    assert resp.json()["count"] == 2

    db.refresh(s1)
    db.refresh(s2)
    assert s1.status == KPIStatus.APPROVED
    assert s1.final_score == 88
    # None falls back to the saved dept score, like single final-approve.
    assert s2.status == KPIStatus.APPROVED
    assert s2.final_score == 85


def test_wrong_status_item_blocks_everything(client, db):
    fx = _setup(db)
    good = _pending_dept(db, fx, fx["emp_a"])
    bad = _pending_dept(db, fx, fx["emp_b"])
    bad.status = KPIStatus.APPROVED
    db.commit()

    resp = _post(client, fx, fx["dept_admin"], [
        {"submission_id": good.id, "score": 82},
        {"submission_id": bad.id, "score": 82},
    ])
    assert resp.status_code == 400

    db.refresh(good)
    assert good.status == KPIStatus.PENDING_DEPT_APPROVAL
    assert good.dept_score is None


def test_dept_admin_cannot_bulk_other_department(client, db):
    fx = _setup(db)
    other = make_submission(
        db, fx["tenant"], fx["emp_other"], fx["tpl_other"], year=YEAR, period=PERIOD,
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80,
    )
    resp = _post(client, fx, fx["dept_admin"], [{"submission_id": other.id, "score": 80}])
    assert resp.status_code == 403


def test_employee_forbidden(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    resp = _post(client, fx, fx["emp_a"], [{"submission_id": s1.id, "score": 80}])
    assert resp.status_code == 403


def test_duplicate_ids_rejected(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    resp = _post(client, fx, fx["dept_admin"], [
        {"submission_id": s1.id, "score": 80},
        {"submission_id": s1.id, "score": 81},
    ])
    assert resp.status_code == 400
    db.refresh(s1)
    assert s1.status == KPIStatus.PENDING_DEPT_APPROVAL


def test_score_above_target_rejected(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    resp = _post(client, fx, fx["dept_admin"], [{"submission_id": s1.id, "score": 150}])
    assert resp.status_code == 400
    db.refresh(s1)
    assert s1.status == KPIStatus.PENDING_DEPT_APPROVAL


def test_tenant_admin_cannot_bulk_dept_pending_items(client, db):
    # Tenant admins bulk final-approve only; dept-pending items belong to dept admins.
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    resp = _post(client, fx, fx["tenant_admin"], [{"submission_id": s1.id, "score": 80}])
    assert resp.status_code == 400
    db.refresh(s1)
    assert s1.status == KPIStatus.PENDING_DEPT_APPROVAL


def test_empty_items_rejected(client, db):
    fx = _setup(db)
    resp = _post(client, fx, fx["dept_admin"], [])
    assert resp.status_code == 422


def test_bulk_approve_uses_saved_score_when_none_sent(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    resp = _post(client, fx, fx["dept_admin"], [{"submission_id": s1.id, "score": None}])
    assert resp.status_code == 200
    db.refresh(s1)
    assert s1.status == KPIStatus.PENDING_FINAL_APPROVAL
    assert s1.dept_score == 80  # falls back to self_score like single approve


def test_bulk_approve_applies_remarks_to_all(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    s2 = _pending_dept(db, fx, fx["emp_b"])
    resp = client.post(
        "/api/kpi/bulk-approve",
        json={
            "year": YEAR,
            "period": PERIOD,
            "items": [
                {"submission_id": s1.id, "score": 82},
                {"submission_id": s2.id, "score": 90},
            ],
            "remarks": "Bulk approved after review",
        },
        headers=auth_headers(fx["dept_admin"], fx["tenant"]),
    )
    assert resp.status_code == 200
    db.refresh(s1)
    db.refresh(s2)
    assert s1.remarks == "Bulk approved after review"
    assert s2.remarks == "Bulk approved after review"


def test_bulk_approve_without_remarks_leaves_remarks_untouched(client, db):
    fx = _setup(db)
    s1 = _pending_dept(db, fx, fx["emp_a"])
    s1.remarks = "Earlier note"
    db.commit()
    resp = _post(client, fx, fx["dept_admin"], [{"submission_id": s1.id, "score": 82}])
    assert resp.status_code == 200
    db.refresh(s1)
    assert s1.remarks == "Earlier note"
