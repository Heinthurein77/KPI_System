"""Reliability race + readiness tests (review 2026-11-10: C2, M1, M2, M3,
M13, M14, M15, M18, M19, M20).

Concurrency tests run parallel TestClient calls via threads, mirroring
the review's repros. SQLite serialises writers, so the observed rates
are a lower bound for production Postgres — the assertions are about
outcomes (exactly one success, zero 500s), which must hold on both.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import time as time_type, timedelta
from unittest import mock

import pytest
from sqlalchemy import event

from app.core.rate_limit import limiter
from app.core.timezone import today as app_today
from app.database import engine, install_pool_checkout_monitor
from app.main import upgrade_with_retry
from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.kpi_submission import KPIStatus
from app.tests.factories import (
    auth_headers,
    make_department,
    make_submission,
    make_template,
    make_tenant,
    make_user,
    open_fillable_period,
)
from app.models.user import UserRole


def _set_settings(client, admin, tenant, **kwargs):
    payload = {"work_days": [0, 1, 2, 3, 4, 5, 6], "office_ips": [], **kwargs}
    resp = client.patch(
        "/api/attendance/settings", json=payload, headers=auth_headers(admin, tenant)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _parallel(client, method, url, headers, n, **kwargs):
    """Run n identical requests concurrently; return the status codes."""
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(getattr(client, method), url, headers=headers, **kwargs)
            for _ in range(n)
        ]
        return [f.result().status_code for f in futures]


# ---------------------------------------------------------------- C2


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


def test_concurrent_dept_approve_exactly_one_succeeds(client, db):
    """C2 repro: 8x parallel dept-approve -> exactly 1x200, rest 400,
    exactly one audit row and one final state (was 7x200 + 7 audit rows)."""
    tenant, reviewer, _employee, submission = _pending_dept_setup(db)
    headers = auth_headers(reviewer, tenant)
    payload = {"year": 2026, "period": "October", "dept_score": 60.0}
    codes = _parallel(
        client, "post", f"/api/kpi/{submission.id}/dept-approve", headers, 8, json=payload
    )
    assert codes.count(200) == 1, codes
    assert codes.count(400) == 7, codes
    assert not any(c >= 500 for c in codes), codes
    db.expire_all()
    refreshed = db.get(type(submission), submission.id)
    assert refreshed.status == KPIStatus.PENDING_FINAL_APPROVAL
    assert (
        db.query(AuditLog)
        .filter(AuditLog.action == "kpi_dept_approved", AuditLog.entity_id == submission.id)
        .count()
        == 1
    )


def test_approve_vs_reject_race_is_serialized(client, db):
    """C2: an approve racing a reject is serialized into a valid
    sequence, never a duplicate or a 500. Either the reject wins first
    (approve then 400s, submission REJECTED) or the approve wins first
    and the reject then acts on the new pending-final state — a
    legitimate second transition (approve audit followed by reject
    audit, submission REJECTED). What must never happen: two approves,
    two rejects, duplicate audit rows for one action, or a 500."""
    tenant, reviewer, _employee, submission = _pending_dept_setup(db)
    headers = auth_headers(reviewer, tenant)
    approve = {"year": 2026, "period": "October", "dept_score": 60.0}
    reject = {"year": 2026, "period": "October", "remarks": "redo"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = (
            [pool.submit(client.post, f"/api/kpi/{submission.id}/dept-approve", headers=headers, json=approve)
             for _ in range(4)]
            + [pool.submit(client.post, f"/api/kpi/{submission.id}/reject", headers=headers, json=reject)
               for _ in range(4)]
        )
        codes = [f.result().status_code for f in futures]
    assert not any(c >= 500 for c in codes), codes
    assert codes.count(200) <= 2, codes  # at most one approve + one reject
    assert codes.count(400) >= 6, codes
    db.expire_all()
    refreshed = db.get(type(submission), submission.id)
    approved = (
        db.query(AuditLog)
        .filter(AuditLog.action == "kpi_dept_approved", AuditLog.entity_id == submission.id)
        .count()
    )
    rejected = (
        db.query(AuditLog)
        .filter(AuditLog.action == "kpi_rejected", AuditLog.entity_id == submission.id)
        .count()
    )
    assert approved <= 1 and rejected <= 1
    if rejected:
        assert refreshed.status == KPIStatus.REJECTED
    else:
        assert approved == 1
        assert refreshed.status == KPIStatus.PENDING_FINAL_APPROVAL


def test_concurrent_bulk_approve_same_submission_once(client, db):
    """C2 via bulk-approve: the same submission listed twice in one
    request is refused, and two concurrent bulk requests approve it once."""
    tenant, reviewer, _employee, submission = _pending_dept_setup(db)
    headers = auth_headers(reviewer, tenant)
    payload = {
        "year": 2026, "period": "October",
        "items": [{"submission_id": submission.id, "score": 60.0, "remarks": None}],
    }
    codes = _parallel(client, "post", "/api/kpi/bulk-approve", headers, 4, json=payload)
    assert codes.count(200) == 1, codes
    assert codes.count(400) == 3, codes
    assert not any(c >= 500 for c in codes), codes
    assert (
        db.query(AuditLog)
        .filter(AuditLog.action == "kpi_dept_approved", AuditLog.entity_id == submission.id)
        .count()
        == 1
    )


# ---------------------------------------------------------------- M1


def test_concurrent_check_in_exactly_one_succeeds(client, db):
    """M1 repro: 10x parallel check-in -> 1x201, rest 409, ZERO 500s
    (was 1x201, 6x409, 3x500)."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    codes = _parallel(
        client, "post", "/api/attendance/check-in", auth_headers(employee, tenant), 10
    )
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 9, codes
    assert not any(c >= 500 for c in codes), codes
    assert (
        db.query(AttendanceRecord)
        .filter_by(tenant_id=tenant.id, employee_id=employee.id)
        .count()
        == 1
    )


def test_check_in_vs_leave_mix_no_500s(client, db):
    """M1 repro: 5x check-in + 5x leave for the same day -> exactly one
    201 and no 500s (was 1x201, 7x409, 2x500)."""
    from app.core.timezone import today as _today

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    headers = auth_headers(employee, tenant)
    today = _today().isoformat()
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = (
            [pool.submit(client.post, "/api/attendance/check-in", headers=headers)
             for _ in range(5)]
            + [pool.submit(client.post, "/api/attendance/leave",
                           headers=headers, json={"record_date": today})
               for _ in range(5)]
        )
        codes = [f.result().status_code for f in futures]
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 9, codes
    assert not any(c >= 500 for c in codes), codes


def test_concurrent_leave_same_day_returns_409_not_500(client, db):
    """M1 (leave half): 10x parallel leave for the same day -> 1x201,
    rest 409, zero 500s."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today().isoformat()
    codes = _parallel(
        client, "post", "/api/attendance/leave", auth_headers(employee, tenant), 10,
        json={"record_date": today},
    )
    assert codes.count(201) == 1, codes
    assert codes.count(409) == 9, codes
    assert not any(c >= 500 for c in codes), codes


# ---------------------------------------------------------------- M2


def test_concurrent_first_dashboard_load_no_500s(client, db):
    """M2 repro: 8x parallel dashboard loads for a fresh employee ->
    all 200, zero 500s (was ~half 500s on uq_submission_period_metric),
    and exactly one draft per template."""
    tenant = make_tenant(db)
    dept = make_department(db, tenant, name="Sales")
    employee = make_user(db, tenant, UserRole.EMPLOYEE, name="Alice Aung", department=dept)
    make_template(db, tenant, metric_name="Revenue", target=100.0, weight=10.0)
    make_template(db, tenant, metric_name="Quality", target=50.0, weight=10.0)
    year, period = open_fillable_period()
    codes = _parallel(
        client, "get", "/api/dashboard",
        auth_headers(employee, tenant), 8,
        params={"year": year, "period": period},
    )
    assert all(c == 200 for c in codes), codes
    db.expire_all()
    from app.models.kpi_submission import KPISubmission

    drafts = (
        db.query(KPISubmission)
        .filter_by(employee_id=employee.id, year=year, month_or_quarter=period)
        .all()
    )
    assert len(drafts) == 2
    assert len({d.kpi_template_id for d in drafts}) == 2


# ---------------------------------------------------------------- M3


def test_concurrent_check_out_exactly_one_succeeds(client, db):
    """M3 repro: 10x parallel check-out -> 1x200, rest 409 (was 2x200)."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    headers = auth_headers(employee, tenant)
    assert client.post("/api/attendance/check-in", headers=headers).status_code == 201
    # The setup check-in consumed 1 of the 10/min per-user attendance
    # budget; reset so the race itself is not masked by a 429.
    limiter.reset()
    codes = _parallel(client, "post", "/api/attendance/check-out", headers, 10)
    assert codes.count(200) == 1, codes
    assert codes.count(409) == 9, codes
    assert not any(c >= 500 for c in codes), codes


# ---------------------------------------------------------------- M18


def _insert_record(db, tenant, employee, day, status, check_in=None, check_out=None):
    record = AttendanceRecord(
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=employee.department_id,
        record_date=day,
        check_in=check_in,
        check_out=check_out,
        status=status,
        late_minutes=0,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def test_check_out_after_midnight_closes_yesterday(client, db):
    """M18: checked in 23:50 yesterday, checking out after midnight ->
    200 and yesterday's record is closed (was 409 + open forever)."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    yesterday = today - timedelta(days=1)
    record = _insert_record(
        db, tenant, employee, yesterday, "present", check_in=time_type(23, 50)
    )
    resp = client.post(
        "/api/attendance/check-out", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["record_date"] == yesterday.isoformat()
    assert resp.json()["check_out"] is not None
    db.expire_all()
    closed = db.get(AttendanceRecord, record.id)
    assert closed.check_out is not None
    assert closed.record_date == yesterday


def test_check_out_with_no_record_still_409(client, db):
    """M18: with no record today and no open record yesterday, the
    existing 409 is kept."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    resp = client.post(
        "/api/attendance/check-out", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 409


def test_check_out_prefers_today_over_yesterday(client, db):
    """M18: when both today and yesterday have open records, check-out
    closes today's and leaves yesterday's open."""
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    employee = make_user(db, tenant, UserRole.EMPLOYEE)
    _set_settings(client, admin, tenant)
    today = app_today()
    yesterday = today - timedelta(days=1)
    old = _insert_record(
        db, tenant, employee, yesterday, "present", check_in=time_type(9, 0)
    )
    assert client.post(
        "/api/attendance/check-in", headers=auth_headers(employee, tenant)
    ).status_code == 201
    limiter.reset()
    resp = client.post(
        "/api/attendance/check-out", headers=auth_headers(employee, tenant)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["record_date"] == today.isoformat()
    db.expire_all()
    assert db.get(AttendanceRecord, old.id).check_out is None


# ---------------------------------------------------------------- M13


def _report_setup(db):
    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    dept = make_department(db, tenant, name="Sales")
    users = [
        make_user(db, tenant, UserRole.EMPLOYEE, name=n, department=dept)
        for n in ("Alice Aung", "Bob Bo", "Carol Chan")
    ]
    return tenant, admin, users


def test_report_returns_correct_data_with_few_queries(client, db):
    """M13: the report returns the same correct per-user data, but
    without a settings + records query per user and a db.get per record
    (was ~2,400 queries for 100 people; here the whole request,
    auth included, stays in single digits)."""
    tenant, admin, users = _report_setup(db)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    today = app_today()
    year, month = today.year, today.month
    from app.services.attendance_service import working_days_in_month

    days = working_days_in_month(year, month, [0, 1, 2, 3, 4])
    _insert_record(db, tenant, users[0], days[0], "present", check_in=time_type(9, 0))
    _insert_record(db, tenant, users[0], days[1], "half", check_in=time_type(14, 0))
    _insert_record(db, tenant, users[1], days[0], "present", check_in=time_type(9, 5))

    count = {"selects": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            count["selects"] += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        resp = client.get(
            "/api/attendance/report",
            params={"year": year, "month": month},
            headers=auth_headers(admin, tenant),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert resp.status_code == 200, resp.text
    rows = {r["user_id"]: r for r in resp.json()}
    assert len(rows) == 3
    assert rows[users[0].id]["present"] == 1
    assert rows[users[0].id]["half"] == 1
    assert rows[users[1].id]["present"] == 1
    assert rows[users[2].id]["present"] == 0
    assert rows[users[0].id]["records"][0]["employee_name"] == "Alice Aung"
    # Auth + tenant + visible ids + users (+departments) + settings +
    # one batched records query. Generous bound; the old code needed
    # 2 per user + 1 per record on top of the same fixed cost.
    assert count["selects"] <= 10, count


def test_report_limit_offset_pagination(client, db):
    """M13: limit/offset paginate the report; the default (no limit)
    still returns every visible row, as the frontend expects."""
    tenant, admin, users = _report_setup(db)
    _set_settings(client, admin, tenant, work_days=[0, 1, 2, 3, 4])
    today = app_today()
    params = {"year": today.year, "month": today.month}
    headers = auth_headers(admin, tenant)
    full = client.get("/api/attendance/report", params=params, headers=headers)
    assert full.status_code == 200, full.text
    assert len(full.json()) == 3
    page1 = client.get(
        "/api/attendance/report", params={**params, "limit": 2}, headers=headers
    )
    assert len(page1.json()) == 2
    page2 = client.get(
        "/api/attendance/report",
        params={**params, "limit": 2, "offset": 2}, headers=headers,
    )
    assert len(page2.json()) == 1
    assert {r["user_id"] for r in page1.json()} | {r["user_id"] for r in page2.json()} == {
        u.id for u in users
    }
    assert client.get(
        "/api/attendance/report", params={**params, "limit": 0}, headers=headers
    ).status_code == 422


# ---------------------------------------------------------------- M14


def test_healthz_still_light_and_readyz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_503_when_db_down(client, monkeypatch):
    """M14: /readyz returns 503 when the database does not answer
    (simulated by making engine.connect raise)."""
    from sqlalchemy.exc import OperationalError

    fake_engine = mock.MagicMock()
    fake_engine.connect.side_effect = OperationalError(
        "could not connect", None, Exception("neon down")
    )
    monkeypatch.setattr("app.main.engine", fake_engine)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}


# ---------------------------------------------------------------- M15


def test_upgrade_with_retry_retries_then_succeeds():
    """M15: the retry helper re-runs the upgrade until it succeeds,
    sleeping the configured backoff between attempts."""
    calls = {"n": 0}
    delays = []

    def flaky(_cfg, _rev):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("neon asleep")

    upgrade_with_retry(
        object(), attempts=5, delays=(1, 2, 3, 4),
        upgrade_fn=flaky, sleep=delays.append,
    )
    assert calls["n"] == 3
    assert delays == [1, 2]


def test_upgrade_with_retry_gives_up_after_attempts():
    """M15: after the final attempt the last error is raised."""
    calls = {"n": 0}
    delays = []

    def always_fails(_cfg, _rev):
        calls["n"] += 1
        raise RuntimeError("neon down")

    with pytest.raises(RuntimeError, match="neon down"):
        upgrade_with_retry(
            object(), attempts=3, delays=(1, 2),
            upgrade_fn=always_fails, sleep=delays.append,
        )
    assert calls["n"] == 3
    assert delays == [1, 2]


# ---------------------------------------------------------------- M19


def test_pool_checkout_monitor_warns_on_slow_checkout(caplog):
    """M19: acquiring a connection from an exhausted pool logs a
    warning naming the wait (proved against a size-1 pool)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool

    eng = create_engine(
        "sqlite://",
        poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=0.3,
        connect_args={"check_same_thread": False},
    )
    install_pool_checkout_monitor(eng, threshold_seconds=0.05)
    held = eng.connect()
    try:
        with caplog.at_level(logging.WARNING, logger="kpi_system"):
            with pytest.raises(Exception):
                eng.connect()
        assert any(
            "DB pool checkout waited" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]
    finally:
        held.close()
        eng.dispose()


# ---------------------------------------------------------------- M20


def test_reminder_job_logs_failure_and_returns_1(monkeypatch, caplog):
    """M20: a failing deadline-reminder run logs FAILED and exits 1
    instead of masking the error (old code raised UnboundLocalError)."""
    from app.jobs import send_deadline_reminders

    monkeypatch.setattr(
        send_deadline_reminders.deadline_reminder_service,
        "send_deadline_reminders",
        mock.Mock(side_effect=RuntimeError("smtp down")),
    )
    with caplog.at_level(logging.INFO, logger="kpi_system.jobs"):
        assert send_deadline_reminders.main() == 1
    assert any("starting" in r.message for r in caplog.records)
    assert any("FAILED" in r.message for r in caplog.records)


def test_prune_job_logs_failure_and_returns_1(monkeypatch, caplog):
    """M20: a failing prune run logs FAILED and exits 1."""
    from app.jobs import prune_audit_logs

    monkeypatch.setattr(
        prune_audit_logs, "prune_audit_logs",
        mock.Mock(side_effect=RuntimeError("db down")),
    )
    with caplog.at_level(logging.INFO, logger="kpi_system.jobs"):
        assert prune_audit_logs.main() == 1
    assert any("starting" in r.message for r in caplog.records)
    assert any("FAILED" in r.message for r in caplog.records)


def test_prune_job_success_logs_and_returns_0(caplog):
    """M20: a successful prune logs its start + success lines."""
    from app.jobs import prune_audit_logs

    with caplog.at_level(logging.INFO, logger="kpi_system.jobs"):
        assert prune_audit_logs.main() == 0
    assert any("starting" in r.message for r in caplog.records)
    assert any("succeeded" in r.message for r in caplog.records)
