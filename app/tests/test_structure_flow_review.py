"""Tests for the senior-review structure & flow findings (2026-11-20).

S-M1 (rollover cron), S-M2 (employee withdraw of a stuck DRAFT),
S-M4 (attendance check-in/out audit), S-M5 (password-change audit),
S-m1 (holiday email), S-m2 (APP_BASE_URL link — see test_email_service),
S-m3 (partial-submit result), S-m4 (self-score audit), S-m7 (TIMEZONE /
ignored env vars — see test_config), S-m8 (unknown status — see
test_attendance for the pure-function cases), S-m9 (notification prune
— see test_prune_audit_logs).
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.jobs.run_recurring_rollover import main as rollover_main
from app.jobs.run_recurring_rollover import run_recurring_rollover
from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.notification import Notification
from app.models.user import UserRole
from app.services import kpi_service
from app.services.attendance_service import monthly_summary
from app.tests.factories import (
    DEFAULT_PASSWORD,
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
)


def _audit(db, action):
    return (
        db.query(AuditLog).filter(AuditLog.action == action).order_by(AuditLog.id).all()
    )


def _setup_recurring(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Recurring Guy", department=dept)
    template = make_template(
        db, tenant, employee=emp, is_recurring=True,
        locked_year=2026, locked_period="September",
    )
    return tenant, dept, emp, template


# ------------------------------------------------------------------ S-M1


def test_rollover_job_materialises_current_period_and_is_idempotent(db):
    _, _, emp, template = _setup_recurring(db)
    result = run_recurring_rollover(db, today=date(2026, 11, 8))
    assert result is not None
    assert result.created == [template.id]
    assert result.skipped == []
    rows = db.scalars(
        select(KPISubmission).where(
            KPISubmission.employee_id == emp.id,
            KPISubmission.year == 2026,
            KPISubmission.month_or_quarter == "November",
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].status == KPIStatus.DRAFT
    # Second run (same day, like a daily cron re-run) creates nothing.
    again = run_recurring_rollover(db, today=date(2026, 11, 8))
    assert again.created == []
    assert again.skipped == [template.id]


def test_rollover_job_does_nothing_before_day_8(db):
    _, _, emp, _template = _setup_recurring(db)
    assert run_recurring_rollover(db, today=date(2026, 11, 3)) is None
    rows = db.scalars(
        select(KPISubmission).where(KPISubmission.employee_id == emp.id)
    ).all()
    assert rows == []


def test_rollover_job_main_returns_zero_and_materialises(db):
    # main() uses the real today (2026-11-20, past day 8) and the test DB.
    _, _, emp, template = _setup_recurring(db)
    assert rollover_main() == 0
    db.expire_all()
    rows = db.scalars(
        select(KPISubmission).where(
            KPISubmission.employee_id == emp.id,
            KPISubmission.kpi_template_id == template.id,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].status == KPIStatus.DRAFT


# ------------------------------------------------------------------ S-M2


def _stuck_draft_setup(db):
    """A post-grace September DRAFT (grace ended 7 Oct 2026) + a
    non-DRAFT row in the same period that must survive, in a period
    totalling 110% weight."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    other = make_user(db, tenant, UserRole.EMPLOYEE, name="Other Emp", department=dept)
    stuck_tpl = make_template(
        db, tenant, metric_name="Stuck Metric", weight=60.0,
        employee=emp, locked_year=2026, locked_period="September",
    )
    keep_tpl = make_template(
        db, tenant, metric_name="Keep Metric", weight=50.0,
        employee=emp, locked_year=2026, locked_period="September",
    )
    other_tpl = make_template(
        db, tenant, metric_name="Other Metric", weight=10.0,
        employee=other, locked_year=2026, locked_period="September",
    )
    stuck = make_submission(
        db, tenant, emp, stuck_tpl, year=2026, period="September", self_score=80.0
    )
    # Already submitted for review — not a DRAFT, so withdraw must not touch it.
    keep = make_submission(
        db, tenant, emp, keep_tpl, year=2026, period="September",
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=70.0,
    )
    other_sub = make_submission(
        db, tenant, other, other_tpl, year=2026, period="September", self_score=90.0
    )
    return tenant, dept, emp, other, stuck, keep, other_sub


def test_withdraw_post_grace_draft_frees_weight_and_audits(db):
    _, _, emp, _, stuck, keep, _ = _stuck_draft_setup(db)
    # 60 + 50 = 110% — the guard refuses the period while the stuck
    # draft exists.
    with pytest.raises(HTTPException):
        kpi_service.validate_period_total_weight(db, emp, 2026, "September")

    result = kpi_service.withdraw_draft(db, emp, 2026, "September", today=date(2026, 10, 15))
    assert result == {"withdrawn": [stuck.id]}

    db.expire_all()
    assert db.get(KPISubmission, stuck.id) is None
    assert db.get(KPISubmission, keep.id) is not None
    assert db.get(KPISubmission, keep.id).status == KPIStatus.PENDING_DEPT_APPROVAL
    # Weight freed: the guard passes again (50% left, not 110%).
    kpi_service.validate_period_total_weight(db, emp, 2026, "September")
    assert kpi_service.period_snapshot_total(db, emp, 2026, "September") == 50
    # Audit row, and no score was invented / nothing approved.
    rows = _audit(db, "kpi_draft_withdrawn")
    assert len(rows) == 1
    assert rows[0].entity_id == stuck.id
    assert rows[0].actor_id == emp.id
    assert "no score was recorded" in rows[0].summary


def test_withdraw_frees_weight_for_a_new_kpi(db):
    """During grace (5 Oct, Sep grace ends 7 Oct) the freed weight lets
    a new KPI be created for the same period — before the withdraw the
    same create is refused at 110% + 40%."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    stuck_tpl = make_template(
        db, tenant, metric_name="Stuck Metric", weight=60.0,
        employee=emp, locked_year=2026, locked_period="September",
    )
    keep_tpl = make_template(
        db, tenant, metric_name="Keep Metric", weight=50.0,
        employee=emp, locked_year=2026, locked_period="September",
    )
    make_submission(db, tenant, emp, stuck_tpl, year=2026, period="September", self_score=80.0)
    make_submission(db, tenant, emp, keep_tpl, year=2026, period="September", self_score=70.0)
    with pytest.raises(HTTPException):
        kpi_service.create_custom_employee_kpi(
            db, admin, emp, "Fresh Metric", 100.0, 40.0,
            2026, "September", today=date(2026, 10, 5),
        )
    # The refused create left an uncommitted template + submission in
    # the session — roll back so the withdraw below sees only the real rows.
    db.rollback()
    kpi_service.withdraw_draft(db, emp, 2026, "September", today=date(2026, 10, 5))
    created = kpi_service.create_custom_employee_kpi(
        db, admin, emp, "Fresh Metric", 100.0, 40.0,
        2026, "September", today=date(2026, 10, 5),
    )
    assert created.status == KPIStatus.DRAFT


def test_withdraw_endpoint_and_no_resurrection_on_dashboard_visit(client, db):
    tenant, _, emp, _, stuck, keep, _ = _stuck_draft_setup(db)
    # Capture ids up front: the endpoint deletes the row through the
    # app's own session, which expires/detaches this session's objects.
    stuck_id, keep_id = stuck.id, keep.id
    resp = client.post(
        "/api/kpi/employee/withdraw",
        json={"year": 2026, "period": "September"},
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["withdrawn"] == [stuck_id]
    assert {s["id"] for s in body["submissions"]} == {keep_id}

    # The next dashboard visit does NOT re-create the withdrawn draft:
    # a post-grace period is not fillable, so ensure_period_submissions
    # is never called for it.
    resp = client.get(
        "/api/dashboard",
        params={"year": 2026, "period": "September"},
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 200
    assert {s["id"] for s in resp.json()["submissions"]} == {keep_id}


def test_withdraw_leaves_other_employees_drafts_untouched(db):
    _, _, emp, other, stuck, _, other_sub = _stuck_draft_setup(db)
    kpi_service.withdraw_draft(db, emp, 2026, "September", today=date(2026, 10, 15))
    db.expire_all()
    assert db.get(KPISubmission, stuck.id) is None
    assert db.get(KPISubmission, other_sub.id) is not None
    assert db.get(KPISubmission, other_sub.id).status == KPIStatus.DRAFT
    # Withdrawing as the other employee finds no drafts of their own in
    # a period where they have none — and never touches emp's rows.
    with pytest.raises(HTTPException) as exc:
        kpi_service.withdraw_draft(db, other, 2026, "August", today=date(2026, 10, 15))
    assert exc.value.status_code == 400


def test_withdraw_non_draft_is_rejected(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    tpl = make_template(db, tenant)
    pending = make_submission(
        db, tenant, emp, tpl, year=2026, period="September",
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=80.0,
    )
    resp = client.post(
        "/api/kpi/employee/withdraw",
        json={"year": 2026, "period": "September"},
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 400
    db.expire_all()
    assert db.get(KPISubmission, pending.id) is not None

    # A tenant admin may not withdraw (they do not self-assess).
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    resp = client.post(
        "/api/kpi/employee/withdraw",
        json={"year": 2026, "period": "September"},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 403


# ------------------------------------------------------------------ S-M4


def _attendance_people(db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    emp = make_user(db, tenant, UserRole.EMPLOYEE)
    return tenant, admin, emp


def test_check_in_and_check_out_are_audited_with_ip(client, db):
    tenant, admin, emp = _attendance_people(db)
    # All 7 days count as working days, no office IPs (gate unenforced).
    resp = client.patch(
        "/api/attendance/settings",
        json={"work_days": [0, 1, 2, 3, 4, 5, 6], "office_ips": []},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text

    headers = dict(auth_headers(emp, tenant))
    headers["X-Forwarded-For"] = "203.0.113.10"
    resp = client.post("/api/attendance/check-in", headers=headers)
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/attendance/check-out", headers=headers)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    check_ins = _audit(db, "attendance_check_in")
    assert len(check_ins) == 1
    assert check_ins[0].actor_id == emp.id
    assert "203.0.113.10" in check_ins[0].summary
    assert "not enforced" in check_ins[0].summary
    check_outs = _audit(db, "attendance_check_out")
    assert len(check_outs) == 1
    assert check_outs[0].actor_id == emp.id
    assert "203.0.113.10" in check_outs[0].summary


# ------------------------------------------------------------------ S-M5


def test_tenant_password_change_is_audited(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPassword456!"},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 204
    rows = _audit(db, "password_changed")
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].entity_id == user.id
    assert "NewPassword456!" not in rows[0].summary
    assert "invalidated" in rows[0].summary


def test_platform_password_change_is_audited(client, db):
    admin = make_user(db, None, UserRole.SUPER_ADMIN, email="root-pw@platform.example.com")
    resp = client.post(
        "/api/platform/auth/change-password",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPassword456!"},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 204
    rows = _audit(db, "password_changed")
    assert len(rows) == 1
    assert rows[0].actor_id == admin.id
    assert "NewPassword456!" not in rows[0].summary


def test_failed_password_change_is_not_audited(client, db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong-password", "new_password": "NewPassword456!"},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 400
    assert _audit(db, "password_changed") == []


# ------------------------------------------------------------------ S-m1


def test_holiday_change_sends_email_after_commit(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    make_user(db, tenant, UserRole.EMPLOYEE, email="emp@acme.example.com")
    from app.routers import attendance as attendance_router

    with patch.object(
        attendance_router.email_service, "send_for_notifications"
    ) as mock_send:
        resp = client.patch(
            "/api/attendance/settings",
            json={"holidays": [{"date": "2026-11-23", "name": "Test Holiday"}]},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 200, resp.text
        mock_send.assert_called_once()
        # The notification objects the mock captured belong to the
        # request's (now closed) session — read them back through the
        # test session instead of touching detached attributes.
        db.expire_all()
        rows = db.query(Notification).filter_by(type="attendance_holiday").all()
        assert len(rows) == 1
        assert rows[0].user_id != admin.id


def test_non_holiday_settings_change_sends_no_email(client, db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    from app.routers import attendance as attendance_router

    with patch.object(
        attendance_router.email_service, "send_for_notifications"
    ) as mock_send:
        resp = client.patch(
            "/api/attendance/settings",
            json={"start_time": "08:30"},
            headers=auth_headers(admin, tenant),
        )
        assert resp.status_code == 200, resp.text
        mock_send.assert_not_called()


# ------------------------------------------------------------------ S-m3


def test_submit_endpoint_returns_submitted_and_skipped(client, db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    t1 = make_template(db, tenant, metric_name="Metric 1")
    t2 = make_template(db, tenant, metric_name="Metric 2")
    draft = make_submission(db, tenant, emp, t1, period="September", self_score=80.0)
    rejected = make_submission(
        db, tenant, emp, t2, period="September",
        status=KPIStatus.REJECTED, self_score=70.0,
    )
    # September 2026 grace ended 7 Oct; the real today (2026-11-20) is
    # past it, so the DRAFT is skipped and only the REJECTED row submits.
    resp = client.post(
        "/api/kpi/employee/submit",
        json={"year": 2026, "period": "September", "scores": {}, "comments": {}},
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["submitted"] == [rejected.id]
    assert body["skipped"] == [draft.id]
    assert {s["id"] for s in body["submissions"]} == {draft.id, rejected.id}
    assert body["submissions"][0]["status"] in ("draft", "pending_dept_approval")


# ------------------------------------------------------------------ S-m4


def test_self_score_save_is_audited(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, department=dept)
    tpl = make_template(db, tenant)
    sub = make_submission(db, tenant, emp, tpl, period="September", self_score=50.0)
    kpi_service.save_self_scores(
        db, emp, {sub.id: 80.0}, today=date(2026, 9, 20)
    )
    rows = _audit(db, "kpi_self_scores_saved")
    assert len(rows) == 1
    assert rows[0].entity_id == sub.id
    assert rows[0].actor_id == emp.id
    assert "50 → 80" in rows[0].summary


# ------------------------------------------------------------------ S-m8


def test_monthly_summary_counts_unknown_status_separately():
    days = [date(2026, 10, d) for d in (1, 2, 5, 6, 7)]
    records = {
        days[0]: "present",
        days[1]: "half",
        days[2]: "leave",
        days[3]: "bogus-status",
        # days[4]: no record at all -> unexplained absence.
    }
    result = monthly_summary(records, days)
    assert result["present"] == 1
    assert result["half"] == 1
    assert result["leave"] == 1
    assert result["unknown"] == 1
    assert result["absent"] == 1  # only the missing record, not the bogus one
    assert result["attendance_pct"] == 30.0


def test_attendance_status_check_constraint_rejects_bad_status(db):
    tenant = make_tenant(db)
    emp = make_user(db, tenant, UserRole.EMPLOYEE)
    rec = AttendanceRecord(
        tenant_id=tenant.id,
        employee_id=emp.id,
        department_id=None,
        record_date=date(2026, 10, 5),
        status="bogus",
    )
    db.add(rec)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
