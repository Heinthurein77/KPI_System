"""Track 2 (security fix-all, 2026-11-25 review): audit coverage and
tamper-resistance.

Covers S-09 (unaudited mutating admin actions + exports), S-10 (audit
tamper-resistance: cascade, prune archive, hash chain, ip_address),
M-01 (distinct password-change audit), M-02 (reset notification +
fellow-admin restriction), M-15 (no emails in logs), M-22 (deletion
retention behaviour) and M-23 (no scores in notification emails),
plus M-40 (rate limits on admin user toggle/edit/delete).
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.jobs.prune_audit_logs import prune_audit_logs
from app.models.audit_log import AuditLog
from app.models.kpi_submission import KPIStatus
from app.models.notification import Notification
from app.models.user import UserRole
from app.services import audit_service, kpi_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _audit_actions(db, client, admin, tenant):
    resp = client.get("/api/admin/audit-log", headers=auth_headers(admin, tenant))
    assert resp.status_code == 200, resp.text
    return [e["action"] for e in resp.json()]


def _audit_rows(db, action=None):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    if action is not None:
        rows = [r for r in rows if r.action == action]
    return rows


# ---------------------------------------------------------------- S-09


def test_s09_department_create_and_rename_are_audited(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/departments", json={"name": "Sales"}, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200, resp.text
    dept_id = resp.json()["id"]

    resp = client.patch(
        f"/api/admin/departments/{dept_id}",
        json={"name": "Sales & Marketing"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    actions = _audit_actions(db, client, admin, tenant)
    assert "department_created" in actions
    assert "department_renamed" in actions


def test_s09_recurring_run_is_audited(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)

    resp = client.post(
        "/api/admin/recurring/run",
        json={"year": 2026, "period": "October"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert _audit_rows(db, "recurring_kpis_run")


def test_s09_annual_pdf_export_is_audited(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.get(
        f"/api/reports/annual/{employee.id}",
        params={"year": 2026},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert _audit_rows(db, "annual_report_exported")


def test_s09_admin_kpi_exports_are_audited(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    resp = client.get(
        "/api/admin/kpi-export",
        params={"employee_id": employee.id, "month": "October", "year": 2026},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    resp = client.get(
        "/api/admin/kpi-export/annual",
        params={"employee_id": employee.id, "year": 2026},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    actions = [r.action for r in _audit_rows(db)]
    assert "kpi_export" in actions
    assert "kpi_annual_export" in actions


# ---------------------------------------------------------------- S-10


def test_s10_audit_rows_are_hash_chained_and_verify(db):
    tenant = make_tenant(db)
    actor = make_user(db, tenant, UserRole.TENANT_ADMIN)
    audit_service.log(db, actor, "user_created", "user", 1, "A", "first")
    audit_service.log(db, actor, "user_created", "user", 2, "B", "second")
    db.commit()

    rows = _audit_rows(db)
    assert len(rows) == 2
    assert all(r.entry_hash for r in rows)
    assert rows[1].previous_hash == rows[0].entry_hash
    ok, bad_id = audit_service.verify_audit_chain(db)
    assert ok is True
    assert bad_id is None


def test_s10_hash_chain_detects_tampering(db):
    tenant = make_tenant(db)
    actor = make_user(db, tenant, UserRole.TENANT_ADMIN)
    audit_service.log(db, actor, "user_created", "user", 1, "A", "first")
    audit_service.log(db, actor, "user_created", "user", 2, "B", "second")
    db.commit()

    rows = _audit_rows(db)
    rows[0].summary = "tampered"
    db.commit()

    ok, bad_id = audit_service.verify_audit_chain(db)
    assert ok is False
    assert bad_id == rows[0].id


def test_s10_tenant_delete_keeps_audit_history(db):
    tenant = make_tenant(db)
    actor = make_user(db, tenant, UserRole.TENANT_ADMIN)
    audit_service.log(db, actor, "user_created", "user", 1, "A", "created")
    db.commit()
    row_id = _audit_rows(db)[0].id

    db.delete(actor)
    db.delete(tenant)
    db.commit()
    db.expunge_all()

    row = db.get(AuditLog, row_id)
    assert row is not None
    assert row.tenant_id is None
    assert row.action == "user_created"


def test_s10_prune_archives_rows_before_deleting(db, tmp_path):
    tenant = make_tenant(db)
    actor = make_user(db, tenant, UserRole.EMPLOYEE)
    old = AuditLog(
        tenant_id=tenant.id,
        actor_id=actor.id,
        actor_name=actor.name,
        actor_role=actor.role.value,
        department_id=None,
        action="kpi_submitted",
        entity_type="kpi_submission",
        entity_id=1,
        entity_label="Test metric",
        summary="old row",
        created_at=datetime.now(timezone.utc) - timedelta(days=200),
    )
    db.add(old)
    db.commit()
    old_id = old.id

    archive = tmp_path / "audit-archive.jsonl"
    deleted = prune_audit_logs(db, retention_days=180, archive_path=archive)
    assert deleted == 1
    db.expunge_all()
    assert db.get(AuditLog, old_id) is None

    lines = archive.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    archived = json.loads(lines[0])
    assert archived["id"] == old_id
    assert archived["action"] == "kpi_submitted"
    assert archived["summary"] == "old row"


def test_s10_ip_address_recorded_when_supplied(db):
    tenant = make_tenant(db)
    actor = make_user(db, tenant, UserRole.TENANT_ADMIN)
    audit_service.log(
        db, actor, "user_created", "user", 1, "A", "with ip", ip_address="203.0.113.7"
    )
    audit_service.log(db, actor, "user_created", "user", 2, "B", "no ip")
    db.commit()

    rows = _audit_rows(db)
    assert rows[0].ip_address == "203.0.113.7"
    assert rows[1].ip_address is None


# ---------------------------------------------------------------- M-01


def test_m01_self_password_change_requires_current_password(db, client):
    from app.core.security import verify_password
    from app.tests.factories import DEFAULT_PASSWORD

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    payload = {
        "name": admin.name,
        "email": admin.email,
        "role": "tenant_admin",
        "department_id": None,
        "password": "NewPassword123",
    }

    # No current password → 400, password unchanged.
    resp = client.put(
        f"/api/admin/users/{admin.id}", json=payload, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 400, resp.text
    db.refresh(admin)
    assert verify_password(DEFAULT_PASSWORD, admin.password_hash)

    # Wrong current password → 400, password unchanged.
    resp = client.put(
        f"/api/admin/users/{admin.id}",
        json={**payload, "current_password": "WrongPassword123"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 400, resp.text
    db.refresh(admin)
    assert verify_password(DEFAULT_PASSWORD, admin.password_hash)

    # Correct current password → 200, distinct audit event, new
    # password in effect. (Further authed calls with the old token
    # would fail on the token_version bump, so assert via the db.)
    resp = client.put(
        f"/api/admin/users/{admin.id}",
        json={**payload, "current_password": DEFAULT_PASSWORD},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    db.refresh(admin)
    assert verify_password("NewPassword123", admin.password_hash)
    assert "user_password_changed" in [r.action for r in _audit_rows(db)]


# ---------------------------------------------------------------- M-38


def test_m38_submissions_endpoint_validates_year_and_period(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    base = f"/api/admin/users/{employee.id}/submissions"
    headers = auth_headers(admin, tenant)
    assert client.get(base, params={"year": 1999, "period": "October"}, headers=headers).status_code == 422
    assert client.get(base, params={"year": 2101, "period": "October"}, headers=headers).status_code == 422
    assert client.get(base, params={"year": 2026, "period": "NotAMonth"}, headers=headers).status_code == 422
    assert client.get(base, params={"year": 2026, "period": "October"}, headers=headers).status_code == 200


def test_m38_exports_validate_year_and_month(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    headers = auth_headers(admin, tenant)

    monthly = "/api/admin/kpi-export"
    assert client.get(
        monthly, params={"employee_id": employee.id, "month": "October", "year": 1999}, headers=headers
    ).status_code == 422
    assert client.get(
        monthly, params={"employee_id": employee.id, "month": "NotAMonth", "year": 2026}, headers=headers
    ).status_code == 422
    assert client.get(
        monthly, params={"employee_id": employee.id, "month": "October", "year": 2026}, headers=headers
    ).status_code == 200

    annual = "/api/admin/kpi-export/annual"
    assert client.get(
        annual, params={"employee_id": employee.id, "year": 1999}, headers=headers
    ).status_code == 422
    assert client.get(
        annual, params={"employee_id": employee.id, "year": 2026}, headers=headers
    ).status_code == 200


def test_m01_password_change_is_a_distinct_audit_event(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    payload = {
        "name": target.name,
        "email": target.email,
        "role": "employee",
        "department_id": dept.id,
        "password": "NewPassword123",
    }

    resp = client.put(
        f"/api/admin/users/{target.id}", json=payload, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200, resp.text
    actions = [r.action for r in _audit_rows(db)]
    assert "user_updated" in actions
    assert "user_password_changed" in actions

    # A password-less edit logs user_updated only.
    other = make_user(db, tenant, UserRole.EMPLOYEE, name="Other", department=dept)
    resp = client.put(
        f"/api/admin/users/{other.id}",
        json={
            "name": "Other Renamed",
            "email": other.email,
            "role": "employee",
            "department_id": dept.id,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert [r.action for r in _audit_rows(db)].count("user_password_changed") == 1


# ---------------------------------------------------------------- M-02


def test_m02_reset_notifies_the_affected_user(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)

    resp = client.post(
        f"/api/admin/users/{target.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    notes = db.scalars(
        select(Notification).where(
            Notification.user_id == target.id, Notification.type == "password_reset"
        )
    ).all()
    assert len(notes) == 1
    assert "reset" in notes[0].title.lower()


def test_m02_tenant_admin_cannot_reset_fellow_tenant_admin(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    fellow = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Fellow Admin")

    resp = client.post(
        f"/api/admin/users/{fellow.id}/reset-password",
        json={"password": "NewPassword123"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 403


def test_m02_tenant_admin_can_still_reset_dept_admin_and_employee(db, client):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)

    for target in (dept_admin, employee):
        resp = client.post(
            f"/api/admin/users/{target.id}/reset-password",
            json={"password": "NewPassword123"},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- M-15


def test_m15_create_user_logs_user_id_not_email(db, client, caplog):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant)

    with caplog.at_level(logging.INFO, logger="kpi_system"):
        resp = client.post(
            "/api/admin/users",
            json={
                "name": "New Hire",
                "email": "new.hire@example.com",
                "password": "Password123!",
                "role": "employee",
                "department_id": dept.id,
            },
            headers=auth_headers(admin, tenant),
        )
    assert resp.status_code == 200, resp.text
    assert "new.hire@example.com" not in caplog.text


# ---------------------------------------------------------------- M-22


def test_m22_force_delete_removes_own_submissions_keeps_audit_names(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE, name="Leaving Emp", department=dept)
    other = make_user(db, tenant, UserRole.EMPLOYEE, name="Other Emp", department=dept)
    template = make_template(db, tenant, department=dept)
    own_sub = make_submission(
        db, tenant, target, template, status=KPIStatus.APPROVED, department=dept
    )
    reviewed_sub = make_submission(
        db, tenant, other, template, status=KPIStatus.APPROVED, department=dept
    )
    reviewed_sub.dept_reviewed_by_id = target.id
    db.commit()

    audit_service.log(db, target, "kpi_submitted", "kpi_submission", own_sub.id, "X", "by leaver")
    db.commit()
    own_sub_id, reviewed_sub_id = own_sub.id, reviewed_sub.id

    kpi_service.force_delete_user(db, admin, target)
    db.expunge_all()

    from app.models.kpi_submission import KPISubmission

    assert db.get(KPISubmission, own_sub_id) is None
    kept = db.get(KPISubmission, reviewed_sub_id)
    assert kept is not None
    assert kept.dept_reviewed_by_id is None
    # Audit snapshots keep the deleted user's name (documented, M-22).
    rows = [r for r in _audit_rows(db) if r.actor_name == "Leaving Emp"]
    assert rows
    assert _audit_rows(db, "user_deleted")


# ---------------------------------------------------------------- M-23


def _notification_message(db, user_id, ntype):
    notes = db.scalars(
        select(Notification).where(
            Notification.user_id == user_id, Notification.type == ntype
        )
    ).all()
    assert len(notes) == 1, [n.type for n in db.scalars(select(Notification)).all()]
    return notes[0].message


def test_m23_final_approval_email_carries_no_score(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=80.0, dept_score=80.0,
    )
    kpi_service.final_approve(db, admin, submission, 87.0, None)
    message = _notification_message(db, employee.id, "kpi_final_approved")
    assert "87" not in message
    assert "dashboard" in message.lower()


def test_m23_override_email_carries_no_score(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    template = make_template(db, tenant, department=dept)
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=80.0, dept_score=80.0,
    )
    kpi_service.tenant_admin_override(db, admin, submission, 93.0, None)
    message = _notification_message(db, employee.id, "kpi_overridden")
    assert "93" not in message
    assert "dashboard" in message.lower()


# ---------------------------------------------------------------- M-40


def test_m40_admin_user_toggle_is_rate_limited(db, client):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    target = make_user(db, tenant, UserRole.EMPLOYEE)

    statuses = []
    for _ in range(11):
        resp = client.post(
            f"/api/admin/users/{target.id}/toggle-active",
            headers=auth_headers(admin, tenant),
        )
        statuses.append(resp.status_code)
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
