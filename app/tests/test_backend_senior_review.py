"""Tests for the backend findings of the KPI senior code review 2026-11-20
(B-M2..B-M7, B-m1..B-m8).

Concurrency tests follow the test_reliability_races.py pattern: parallel
TestClient calls via threads. SQLite serialises writers, so the observed
rates are a lower bound for production Postgres — the assertions are
about outcomes (no 500s, audit rows matching successful calls), which
must hold on both.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.core.timezone import today as app_today
from app.database import engine
from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.kpi_submission import KPIStatus
from app.models.notification import Notification
from app.models.user import UserRole
from app.services.kpi_service import _claim_submission, save_self_scores
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
    open_fillable_period,
)


def _parallel(client, method, url, headers, n, **kwargs):
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(getattr(client, method), url, headers=headers, **kwargs)
            for _ in range(n)
        ]
        return [f.result().status_code for f in futures]


def _audit_count(db, action, entity_id=None):
    q = db.query(AuditLog).filter(AuditLog.action == action)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    return q.count()


# ---------------------------------------------------------------- B-M2


def _draft_setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    reviewer = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Dept", department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=dept
    )
    year, period = open_fillable_period()
    submission = make_submission(
        db, tenant, employee, template, year=year, period=period,
        status=KPIStatus.DRAFT, self_score=50.0, department=dept,
    )
    return tenant, dept, reviewer, employee, submission, year, period


def test_concurrent_double_submit_yields_one_set_of_side_effects(client, db):
    """B-M2: 8x parallel employee-submit -> exactly 1x200, rest 400,
    exactly one kpi_submitted audit row and one notification (was:
    every caller notified, audit rows duplicated)."""
    tenant, _dept, _reviewer, employee, submission, year, period = _draft_setup(db)
    headers = auth_headers(employee, tenant)
    payload = {"year": year, "period": period, "scores": {}, "comments": {}}
    codes = _parallel(client, "post", "/api/kpi/employee/submit", headers, 8, json=payload)
    assert codes.count(200) == 1, codes
    assert codes.count(400) == 7, codes
    assert not any(c >= 500 for c in codes), codes
    db.expire_all()
    assert _audit_count(db, "kpi_submitted", submission.id) == 1
    assert (
        db.query(Notification)
        .filter(Notification.type == "kpi_submitted")
        .count()
        == 1
    )
    assert db.get(type(submission), submission.id).status == KPIStatus.PENDING_DEPT_APPROVAL


def test_stale_save_fails_after_concurrent_commit(db):
    """B-M2 (deterministic): two sessions race a save. Session 2 saves
    and commits first; session 1, holding the pre-write snapshot, then
    fails its versioned claim with 400 instead of silently overwriting
    session 2's scores.

    Note: there is deliberately no HTTP-level exact-count test for
    saves. Two simultaneous TestClient requests often serialize
    completely under the GIL, so the "loser" legitimately loads after
    the winner commits and succeeds — a sequential save, not a race.
    The status-based double-submit test above does not have this
    problem (a late loader fails the status check whatever the timing),
    and this test pins the save guard's mechanism with a controlled
    interleaving instead."""
    from app.database import SessionLocal
    from app.models.user import User

    tenant, _dept, _reviewer, employee, submission, year, period = _draft_setup(db)
    stale_updated_at = submission.updated_at
    other = SessionLocal()
    try:
        employee2 = other.get(User, employee.id)
        save_self_scores(other, employee2, {submission.id: 55.0})
    finally:
        other.close()
    db.expire_all()
    refreshed = db.get(type(submission), submission.id)
    assert refreshed.self_score == 55.0
    with pytest.raises(HTTPException) as exc:
        _claim_submission(
            db, refreshed, [KPIStatus.DRAFT, KPIStatus.REJECTED],
            "These scores were changed by another request. Reload and try again.",
            expected_updated_at=stale_updated_at,
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------- B-M3


def test_concurrent_custom_creates_cannot_push_period_over_100(client, db):
    """B-M3: 8x parallel custom-KPI creates of weight 60 for one
    employee -> exactly 1x200, rest 400, period total stays 60 (was:
    both could observe <100 and commit, leaving the period at 120)."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    year, period = open_fillable_period()
    headers = auth_headers(admin, tenant)

    def _create(i):
        return client.post(
            "/api/admin/templates/custom",
            json={
                "employee_id": employee.id,
                "metric_name": f"Metric {i}",
                "target": 100,
                "weight": 60,
                "year": year,
                "period": period,
                "is_recurring": False,
            },
            headers=headers,
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(_create, range(8)))
    assert codes.count(200) == 1, codes
    assert codes.count(400) == 7, codes
    assert not any(c >= 500 for c in codes), codes
    db.expire_all()
    from app.services import kpi_service

    total = kpi_service.period_snapshot_total(db, employee, year, period)
    assert float(total) == 60.0


# ---------------------------------------------------------------- B-M4


def _pending_final_setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=dept
    )
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_FINAL_APPROVAL, self_score=50.0, department=dept,
    )
    return tenant, admin, employee, submission


def test_override_on_already_approved_corrects_score(client, db):
    """Post-approval correction (user decision 2026-11-20): a tenant
    admin may correct an already-APPROVED row's score via override.
    The row stays APPROVED, the new score and remarks are stored, and
    the correction is audit-logged ("Score corrected after approval")."""
    tenant, admin, _employee, submission = _pending_final_setup(db)
    headers = auth_headers(admin, tenant)
    payload = {"year": 2026, "period": "October", "final_score": 70.0}
    first = client.post(f"/api/kpi/{submission.id}/override", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert _audit_count(db, "kpi_overridden", submission.id) == 1
    correction = {"year": 2026, "period": "October", "final_score": 85.0, "remarks": "Correction"}
    second = client.post(f"/api/kpi/{submission.id}/override", json=correction, headers=headers)
    assert second.status_code == 200, second.text
    assert _audit_count(db, "kpi_overridden", submission.id) == 2
    db.expire_all()
    s = db.get(type(submission), submission.id)
    assert s.status == KPIStatus.APPROVED
    assert s.final_score == 85.0
    assert s.remarks == "Correction"
    summaries = [
        r.summary for r in db.query(AuditLog).filter(
            AuditLog.action == "kpi_overridden", AuditLog.entity_id == submission.id
        ).order_by(AuditLog.id).all()
    ]
    assert "corrected after approval" in summaries[-1]


def test_override_racing_final_approve_serialises_without_500s(client, db):
    """Post-approval correction (user decision 2026-11-20): an override
    racing a final approve no longer has a single winner. Every
    override succeeds — the first as the approval, the rest as
    corrections — while a final approve only wins if it claims the row
    before any override does (otherwise 400). The claim still
    serialises the race: no 500s, at most one final-approval audit row,
    and exactly one override audit row per override that returned 200."""
    tenant, admin, _employee, submission = _pending_final_setup(db)
    headers = auth_headers(admin, tenant)
    override = {"year": 2026, "period": "October", "final_score": 70.0}
    approve = {"year": 2026, "period": "October", "final_score": 70.0}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = (
            [pool.submit(client.post, f"/api/kpi/{submission.id}/override", headers=headers, json=override)
             for _ in range(4)]
            + [pool.submit(client.post, f"/api/kpi/{submission.id}/final-approve", headers=headers, json=approve)
               for _ in range(4)]
        )
        codes = [f.result().status_code for f in futures]
    assert not any(c >= 500 for c in codes), codes
    db.expire_all()
    assert db.get(type(submission), submission.id).status == KPIStatus.APPROVED
    assert _audit_count(db, "kpi_overridden", submission.id) == 4
    final_approved = _audit_count(db, "kpi_final_approved", submission.id)
    assert final_approved in (0, 1)
    assert codes.count(200) == 4 + final_approved, codes


# ---------------------------------------------------------------- B-M5


def test_department_transfer_moves_submission_and_attendance_visibility(client, db):
    """B-M5: transferring an employee moves their KPI submissions and
    attendance rows to the new department — the old dept admin loses
    visibility/approval, the new one gains it."""
    tenant = make_tenant(db)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Ops")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    old_da = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Sales", department=sales)
    new_da = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Oscar Ops", department=ops)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=sales)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=sales
    )
    year, period = open_fillable_period()
    submission = make_submission(
        db, tenant, employee, template, year=year, period=period,
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=50.0, department=sales,
    )
    record = AttendanceRecord(
        tenant_id=tenant.id, employee_id=employee.id, department_id=sales.id,
        record_date=app_today(), status="present",
    )
    db.add(record)
    db.commit()

    resp = client.put(
        f"/api/admin/users/{employee.id}",
        json={
            "name": employee.name, "email": employee.email, "role": "employee",
            "department_id": ops.id, "position": None, "password": None,
        },
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    db.expire_all()
    assert db.get(type(submission), submission.id).department_id == ops.id
    assert db.get(AttendanceRecord, record.id).department_id == ops.id

    # Old dept admin: the submission is gone from their board, and the
    # per-user detail endpoint 403s.
    board = client.get(
        "/api/dashboard", params={"year": year, "period": period},
        headers=auth_headers(old_da, tenant),
    )
    assert board.status_code == 200
    assert all(s["id"] != submission.id for s in board.json()["submissions"])
    detail = client.get(
        f"/api/admin/users/{employee.id}/submissions",
        params={"year": year, "period": period},
        headers=auth_headers(old_da, tenant),
    )
    assert detail.status_code == 403

    # New dept admin: the submission is on their board.
    board = client.get(
        "/api/dashboard", params={"year": year, "period": period},
        headers=auth_headers(new_da, tenant),
    )
    assert board.status_code == 200
    assert any(s["id"] == submission.id for s in board.json()["submissions"])


# ---------------------------------------------------------------- B-M6


def _pending_dept_setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    reviewer = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Dept", department=dept)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=dept
    )
    submission = make_submission(
        db, tenant, employee, template, year=2026, period="October",
        status=KPIStatus.PENDING_DEPT_APPROVAL, self_score=50.0, department=dept,
    )
    return tenant, reviewer, employee, submission


def test_single_item_review_rejects_period_mismatch(client, db):
    """B-M6: approving a submission while claiming a different
    year/period is a 422 (was: 200, the payload ignored) — and the
    matching period still works."""
    tenant, reviewer, _employee, submission = _pending_dept_setup(db)
    headers = auth_headers(reviewer, tenant)
    wrong = client.post(
        f"/api/kpi/{submission.id}/dept-approve",
        json={"year": 2020, "period": "January", "dept_score": 60.0},
        headers=headers,
    )
    assert wrong.status_code == 422, wrong.text
    ok = client.post(
        f"/api/kpi/{submission.id}/dept-approve",
        json={"year": 2026, "period": "October", "dept_score": 60.0},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------- B-m1


def test_year_period_validation_is_unified(client, db):
    """B-m1: the same bad year/period is a 422 on the save, dashboard
    and analytics endpoints (was: different ranges/codes per endpoint,
    and dashboard/analytics never validated the year at all)."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    save = client.post(
        "/api/kpi/employee/save",
        json={"year": 1999, "period": "November", "scores": {}},
        headers=auth_headers(employee, tenant),
    )
    assert save.status_code == 422, save.text
    save = client.post(
        "/api/kpi/employee/save",
        json={"year": 2026, "period": "Someday", "scores": {}},
        headers=auth_headers(employee, tenant),
    )
    assert save.status_code == 422, save.text
    dash = client.get(
        "/api/dashboard", params={"year": 1999, "period": "November"},
        headers=auth_headers(admin, tenant),
    )
    assert dash.status_code == 422, dash.text
    overview = client.get(
        "/api/analytics/overview", params={"year": 2026, "period": "Someday"},
        headers=auth_headers(admin, tenant),
    )
    assert overview.status_code == 422, overview.text


# ---------------------------------------------------------------- B-m2


def test_blank_names_rejected_on_create_paths(client, db):
    """B-m2: whitespace-only names 422 on create (department, user,
    template) — matching the rename paths that already rejected them."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    headers = auth_headers(admin, tenant)
    dept = client.post("/api/admin/departments", json={"name": "   "}, headers=headers)
    assert dept.status_code == 422, dept.text
    user = client.post(
        "/api/admin/users",
        json={
            "name": "  ", "email": "blank@example.com", "password": "Password123!",
            "role": "employee", "department_id": None,
        },
        headers=headers,
    )
    assert user.status_code == 422, user.text
    template = client.post(
        "/api/admin/templates",
        json={"metric_name": "  ", "target": 100, "weight": 10},
        headers=headers,
    )
    assert template.status_code == 422, template.text


# ---------------------------------------------------------------- B-m3


def test_annual_export_survives_non_month_period_label(client, db):
    """B-m3: a legacy quarterly period label in the DB must not 500
    the annual export (MONTH_NAMES.index raised ValueError)."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=dept
    )
    make_submission(
        db, tenant, employee, template, year=2026, period="Q1",
        status=KPIStatus.APPROVED, self_score=50.0, final_score=50.0, department=dept,
    )
    resp = client.get(
        "/api/admin/kpi-export/annual",
        params={"employee_id": employee.id, "year": 2026},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- B-m4


def test_user_and_tenant_lifecycle_writes_audit_rows(client, db):
    """B-m4: create/edit/toggle user and create/suspend/activate
    tenant each leave an audit row (were unaudited)."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    headers = auth_headers(admin, tenant)

    created = client.post(
        "/api/admin/users",
        json={
            "name": "Bob New", "email": "bob@example.com", "password": "Password123!",
            "role": "employee", "department_id": dept.id,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]
    assert _audit_count(db, "user_created", user_id) == 1

    edited = client.put(
        f"/api/admin/users/{user_id}",
        json={
            "name": "Bob Renamed", "email": "bob@example.com", "role": "employee",
            "department_id": dept.id, "position": None, "password": None,
        },
        headers=headers,
    )
    assert edited.status_code == 200, edited.text
    assert _audit_count(db, "user_updated", user_id) == 1

    toggled = client.post(f"/api/admin/users/{user_id}/toggle-active", headers=headers)
    assert toggled.status_code == 200, toggled.text
    assert _audit_count(db, "user_deactivated", user_id) == 1

    platform = make_user(db, None, UserRole.SUPER_ADMIN, email="root@platform.example.com")
    pheaders = auth_headers(platform)
    new_tenant = client.post(
        "/api/platform/tenants",
        json={
            "name": "Beta", "slug": "beta",
            "admin_name": "Beta Admin", "admin_email": "beta-admin@example.com",
            "admin_password": "Password123!",
        },
        headers=pheaders,
    )
    assert new_tenant.status_code == 200, new_tenant.text
    tenant_id = new_tenant.json()["id"]
    assert _audit_count(db, "tenant_created", tenant_id) == 1
    suspended = client.post(f"/api/platform/tenants/{tenant_id}/suspend", headers=pheaders)
    assert suspended.status_code == 200, suspended.text
    assert _audit_count(db, "tenant_suspended", tenant_id) == 1
    activated = client.post(f"/api/platform/tenants/{tenant_id}/activate", headers=pheaders)
    assert activated.status_code == 200, activated.text
    assert _audit_count(db, "tenant_activated", tenant_id) == 1


# ---------------------------------------------------------------- B-m5


def test_notifications_limit_clamped_below(client, db):
    """B-m5: GET /api/notifications?limit=-5 clamps to 1 like the
    audit-log endpoints (was: the negative limit passed to the DB)."""
    tenant = make_tenant(db)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung")
    from app.tests.factories import make_notification

    make_notification(db, tenant, employee)
    resp = client.get(
        "/api/notifications", params={"limit": -5},
        headers=auth_headers(employee, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["notifications"]) == 1


# ---------------------------------------------------------------- B-m6


def test_dashboard_rejects_other_department_for_dept_admin(client, db):
    """B-m6: a dept admin passing another department's id gets a 403
    (was: 200 with the filter silently dropped); their own id is fine."""
    tenant = make_tenant(db)
    sales = make_department(db, tenant, name="Sales")
    ops = make_department(db, tenant, name="Ops")
    dept_admin = make_user(db, tenant, UserRole.DEPT_ADMIN, name="Dana Sales", department=sales)
    headers = auth_headers(dept_admin, tenant)
    other = client.get(
        "/api/dashboard", params={"department_id": ops.id}, headers=headers
    )
    assert other.status_code == 403, other.text
    own = client.get(
        "/api/dashboard", params={"department_id": sales.id}, headers=headers
    )
    assert own.status_code == 200, own.text


# ---------------------------------------------------------------- B-M7


def test_list_users_and_templates_are_bounded(client, db):
    """B-M7: list_users / list_templates honour limit (default 100,
    max 500) instead of loading the whole tenant."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    for i in range(3):
        make_user(db, tenant, UserRole.EMPLOYEE, name=f"Emp {i}", department=dept)
        make_template(db, tenant, metric_name=f"Metric {i}", target=100.0, weight=10.0)
    headers = auth_headers(admin, tenant)
    users = client.get("/api/admin/users", params={"limit": 2}, headers=headers)
    assert users.status_code == 200
    assert len(users.json()["users"]) == 2
    users = client.get("/api/admin/users", params={"limit": 0}, headers=headers)
    assert users.status_code == 200
    assert len(users.json()["users"]) == 1  # clamped to 1, not 0
    templates = client.get("/api/admin/templates", params={"limit": 1}, headers=headers)
    assert templates.status_code == 200
    assert len(templates.json()["kpi_templates"]) == 1


def test_analytics_trend_issues_one_submissions_query(client, db):
    """B-M7: the trend endpoint fetches all requested months in one
    range query (was: one query per month, up to 24)."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=dept
    )
    today = app_today()
    make_submission(
        db, tenant, employee, template, year=today.year, period="January",
        status=KPIStatus.APPROVED, self_score=50.0, final_score=50.0, department=dept,
    )
    count = {"selects": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            count["selects"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        resp = client.get(
            "/api/analytics/trend", params={"periods": 12},
            headers=auth_headers(admin, tenant),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert resp.status_code == 200, resp.text
    # Auth + tenant resolution + the single range query: single digits.
    # (Before the fix this was 12 per-month queries on top of auth.)
    assert count["selects"] <= 8, count


def test_employee_dashboard_load_does_not_n_plus_1(client, db):
    """B-M7: an employee dashboard load with 6 submissions stays in
    single-digit SELECTs — no lazy load per row on sort/serialise."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    year, period = open_fillable_period()
    for i in range(6):
        template = make_template(
            db, tenant, metric_name=f"Metric {i}", target=100.0, weight=10.0, department=dept
        )
        make_submission(
            db, tenant, employee, template, year=year, period=period,
            status=KPIStatus.DRAFT, self_score=50.0, department=dept,
        )
    count = {"selects": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            count["selects"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        resp = client.get(
            "/api/dashboard", params={"year": year, "period": period},
            headers=auth_headers(employee, tenant),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["submissions"]) == 6
    assert count["selects"] <= 12, count


# ---------------------------------------------------------------- B-m7


def test_safe_file_part_is_shared_and_strict(client, db):
    """B-m7: one shared _safe_file_part (the stricter ASCII rule) backs
    both the attendance and the reports export — a Burmese name falls
    back instead of slugging differently per endpoint."""
    from app.routers.attendance import _safe_file_part as att_part
    from app.routers.reports import _safe_file_part as reports_part
    from app.services.excel_safe import safe_file_part

    assert safe_file_part("Aung San") == "aung-san"
    assert safe_file_part("Sales & Ops!") == "sales---ops"
    assert safe_file_part("အောင်ဆန်း") == "employee"
    assert safe_file_part("အောင်ဆန်း", "department") == "department"
    assert att_part is safe_file_part
    assert reports_part is safe_file_part


# ---------------------------------------------------------------- B-m8


def test_over_target_employee_still_appears_on_team_board(client, db):
    """B-m8: an employee whose only final-approved row is over target
    still appears in employee_combined, flagged — they no longer
    vanish from the board."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Tina Admin")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    template = make_template(
        db, tenant, metric_name="Revenue", target=100.0, weight=10.0, department=dept
    )
    year, period = open_fillable_period()
    make_submission(
        db, tenant, employee, template, year=year, period=period,
        status=KPIStatus.APPROVED, self_score=50.0, final_score=150.0, department=dept,
    )
    resp = client.get(
        "/api/dashboard", params={"year": year, "period": period},
        headers=auth_headers(admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    combined = resp.json()["employee_combined"][str(employee.id)]
    assert combined is not None
    assert combined["has_over_target_row"] is True
    assert combined["attainment"] is None
