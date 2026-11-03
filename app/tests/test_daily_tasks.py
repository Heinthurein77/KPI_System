"""Tests for the Daily Task diary module (additive-only).

Covers: entry CRUD + role scoping, supervisor feedback + notification,
monthly summary counts, Excel export shape, and the evening reminder
service's once-per-day idempotency.
"""

from datetime import date
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.models.user import UserRole
from app.services import daily_task_reminder_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)


def _setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    emp = make_user(db, tenant, UserRole.EMPLOYEE, name="Diary Emp", department=dept)
    dept_admin = make_user(
        db, tenant, UserRole.DEPT_ADMIN, name="Diary Boss", department=dept
    )
    tenant_admin = make_user(db, tenant, UserRole.TENANT_ADMIN, name="Diary Admin")
    return tenant, dept, emp, dept_admin, tenant_admin


def _entry_payload(**overrides):
    payload = {
        "task_date": "2026-11-02",
        "plan_linkage": "November Plan — Monthly Report",
        "work_done": "October Monthly Report draft finished",
        "status": "in_progress",
        "figure_output": "Report 1",
        "evidence_link": "Monthly_Report_Oct.docx",
        "difficulties": "BU data late",
        "support_needed": "HOD reminder",
        "self_reflection": "Ask data earlier",
        "tomorrow_plan_1": "BU follow-up",
        "tomorrow_plan_2": "Report final",
        "tomorrow_plan_3": "KPI data check",
    }
    payload.update(overrides)
    return payload


def test_employee_creates_and_lists_own_entry(db, client):
    tenant, _, emp, _, _ = _setup(db)
    resp = client.post(
        "/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["work_done"].startswith("October Monthly")
    assert body["author_name"] == "Diary Emp"
    assert body["has_evidence_file"] is False

    resp = client.get(
        "/api/daily-tasks?year=2026&month=11", headers=auth_headers(emp, tenant)
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_employee_cannot_see_other_users_entries(db, client):
    tenant, dept, emp, _, _ = _setup(db)
    # Second employee in the same tenant/department.
    other = make_user(db, tenant, UserRole.EMPLOYEE, name="Other Emp", department=dept)
    resp = client.post(
        "/api/daily-tasks", json=_entry_payload(), headers=auth_headers(other, tenant)
    )
    assert resp.status_code == 201
    resp = client.get(
        "/api/daily-tasks?year=2026&month=11", headers=auth_headers(emp, tenant)
    )
    assert resp.status_code == 200
    assert resp.json() == []
    resp = client.get(
        f"/api/daily-tasks?year=2026&month=11&user_id={other.id}",
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 403


def test_dept_admin_feedback_notifies_employee(db, client):
    tenant, _, emp, boss, _ = _setup(db)
    resp = client.post(
        "/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant)
    )
    entry_id = resp.json()["id"]
    resp = client.post(
        f"/api/daily-tasks/{entry_id}/feedback",
        json={"supervisor_feedback": "Good progress. Remind sent."},
        headers=auth_headers(boss, tenant),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["supervisor_feedback"].startswith("Good progress")
    notes = db.scalars(
        select(Notification).where(
            Notification.user_id == emp.id, Notification.type == "daily_task_feedback"
        )
    ).all()
    assert len(notes) == 1


def test_employee_cannot_give_own_feedback(db, client):
    tenant, _, emp, _, _ = _setup(db)
    resp = client.post(
        "/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant)
    )
    entry_id = resp.json()["id"]
    resp = client.post(
        f"/api/daily-tasks/{entry_id}/feedback",
        json={"supervisor_feedback": "self praise"},
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 403


def test_monthly_summary_counts(db, client):
    tenant, _, emp, _, tenant_admin = _setup(db)
    client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    client.post(
        "/api/daily-tasks",
        json=_entry_payload(task_date="2026-11-03", status="completed", work_done="Next day task"),
        headers=auth_headers(emp, tenant),
    )
    client.post(
        "/api/daily-tasks",
        json=_entry_payload(task_date="2026-11-04", status="completed", work_done="Third day task"),
        headers=auth_headers(emp, tenant),
    )
    resp = client.get(
        "/api/daily-tasks/summary/month?year=2026&month=11",
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200, resp.text
    row = [u for u in resp.json()["users"] if u["user_id"] == emp.id][0]
    assert row["working_days"] == 3  # three distinct dates
    assert row["completed"] == 2
    assert row["in_progress_or_pending"] == 1
    assert row["total"] == 3


def test_export_returns_workbook(db, client):
    tenant, _, emp, _, _ = _setup(db)
    client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    resp = client.get(
        "/api/daily-tasks/export/file?year=2026&month=11",
        headers=auth_headers(emp, tenant),
    )
    assert resp.status_code == 200, resp.text
    wb = openpyxl.load_workbook(BytesIO(resp.content))
    assert "Monthly Dashboard" in wb.sheetnames
    diary = wb.sheetnames[0]
    ws = wb[diary]
    assert ws["A1"].value.startswith("HEAD OFFICE - DAILY WORKING DIARY")
    # Header row is row 5; the entry row follows.
    assert ws.cell(row=6, column=3).value.startswith("October Monthly")


def test_evening_reminder_idempotent(db):
    tenant, _, emp, _, _ = _setup(db)
    result = daily_task_reminder_service.send_daily_task_reminders(db, today=date(2026, 11, 2))
    assert emp.email in result["reminded"]
    result2 = daily_task_reminder_service.send_daily_task_reminders(db, today=date(2026, 11, 2))
    assert result2["reminded"] == []
    notes = db.scalars(
        select(Notification).where(
            Notification.user_id == emp.id, Notification.type == "daily_task_reminder"
        )
    ).all()
    assert len(notes) == 1


def test_tenant_admin_cannot_create_entry(db, client):
    tenant, _, _, _, tenant_admin = _setup(db)
    resp = client.post(
        "/api/daily-tasks", json=_entry_payload(), headers=auth_headers(tenant_admin, tenant)
    )
    assert resp.status_code == 403


def test_monthly_summary_excludes_tenant_admin(db, client):
    tenant, _, emp, _, tenant_admin = _setup(db)
    client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    resp = client.get(
        "/api/daily-tasks/summary/month?year=2026&month=11",
        headers=auth_headers(tenant_admin, tenant),
    )
    assert resp.status_code == 200
    assert all(u["user_id"] != tenant_admin.id for u in resp.json()["users"])
    assert any(u["user_id"] == emp.id for u in resp.json()["users"])


def test_evening_reminder_skips_weekend(db):
    _, _, emp, _, _ = _setup(db)
    # 2026-11-07 is a Saturday.
    result = daily_task_reminder_service.send_daily_task_reminders(db, today=date(2026, 11, 7))
    assert result["reminded"] == []
    notes = db.scalars(
        select(Notification).where(
            Notification.user_id == emp.id, Notification.type == "daily_task_reminder"
        )
    ).all()
    assert notes == []


def test_evening_reminder_skips_filled_diary(db, client):
    tenant, _, emp, _, _ = _setup(db)
    client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    result = daily_task_reminder_service.send_daily_task_reminders(db, today=date(2026, 11, 2))
    assert emp.email in result["skipped"]
    assert emp.email not in result["reminded"]


def test_diary_audit_log_records_who_changed_what(db, client):
    tenant, _, emp, boss, tenant_admin = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    entry_id = r.json()["id"]
    client.patch(f"/api/daily-tasks/{entry_id}", json={"status": "completed"}, headers=auth_headers(emp, tenant))
    client.post(f"/api/daily-tasks/{entry_id}/feedback",
                json={"supervisor_feedback": "Good"}, headers=auth_headers(boss, tenant))
    r = client.get("/api/daily-tasks/audit-log?year=2026&month=11",
                   headers=auth_headers(tenant_admin, tenant))
    assert r.status_code == 200, r.text
    actions = [e["action"] for e in r.json()]
    assert "daily_task_created" in actions
    assert "daily_task_updated" in actions
    assert "daily_task_feedback" in actions
    update = [e for e in r.json() if e["action"] == "daily_task_updated"][0]
    assert "status" in update["summary"]
    assert update["actor_name"] == "Diary Emp"
    feedback = [e for e in r.json() if e["action"] == "daily_task_feedback"][0]
    assert feedback["actor_name"] == "Diary Boss"


def test_diary_audit_log_tenant_admin_only(db, client):
    tenant, dept, emp, boss, tenant_admin = _setup(db)
    client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    # Dept admins and employees must NOT see the trail at all.
    r = client.get("/api/daily-tasks/audit-log?year=2026&month=11",
                   headers=auth_headers(boss, tenant))
    assert r.status_code == 403
    r = client.get("/api/daily-tasks/audit-log?year=2026&month=11",
                   headers=auth_headers(emp, tenant))
    assert r.status_code == 403
    # Tenant admin sees everything.
    r = client.get("/api/daily-tasks/audit-log?year=2026&month=11",
                   headers=auth_headers(tenant_admin, tenant))
    assert r.status_code == 200
    assert any(e["action"] == "daily_task_created" for e in r.json())


def test_diary_audit_log_shows_old_new_and_deleted_text(db, client):
    tenant, _, emp, _, tenant_admin = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    entry_id = r.json()["id"]
    client.patch(f"/api/daily-tasks/{entry_id}", json={"status": "completed"},
                 headers=auth_headers(emp, tenant))
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))
    r = client.get("/api/daily-tasks/audit-log?year=2026&month=11",
                   headers=auth_headers(tenant_admin, tenant))
    assert r.status_code == 200
    update = [e for e in r.json() if e["action"] == "daily_task_updated"][0]
    assert "in_progress" in update["summary"] and "completed" in update["summary"]
    deleted = [e for e in r.json() if e["action"] == "daily_task_deleted"][0]
    assert "October Monthly Report draft finished" in deleted["summary"]


def test_evidence_file_served_inline_for_viewing(db, client):
    tenant, _, emp, _, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    entry_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = client.post(f"/api/daily-tasks/{entry_id}/evidence",
                    files={"file": ("shot.png", png, "image/png")},
                    headers=auth_headers(emp, tenant))
    assert r.status_code == 200, r.text
    assert r.json()["has_evidence_file"] is True
    r = client.get(f"/api/daily-tasks/{entry_id}/evidence", headers=auth_headers(emp, tenant))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content == png
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def test_evidence_upload_storage_failure_says_so_clearly(db, client, monkeypatch):
    """If the uploads folder is not writable, the upload must fail with a
    clear storage message — not the generic 500 handler text."""
    from app.routers import daily_tasks as router

    tenant, _, emp, _, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    entry_id = r.json()["id"]
    monkeypatch.setattr(router, "UPLOAD_DIR", Path("/proc/definitely-not-writable"))
    r = client.post(f"/api/daily-tasks/{entry_id}/evidence",
                    files={"file": ("shot.png", b"\x89PNG\r\n\x1a\n", "image/png")},
                    headers=auth_headers(emp, tenant))
    assert r.status_code == 500
    assert "Could not save the evidence file" in r.json()["detail"]
    assert "unexpected error" not in r.json()["detail"].lower()
    # The entry itself is untouched and still readable.
    r = client.get("/api/daily-tasks?year=2026&month=11",
                   headers=auth_headers(emp, tenant))
    assert r.status_code == 200


def _bearer_only(user):
    """Headers a plain browser navigation can never send: a session token
    but NO X-Tenant-Slug (navigations, <img> and <iframe> carry no custom
    headers)."""
    from app.core.security import create_session_token

    return {"Authorization": f"Bearer {create_session_token(user.id, user.tenant_id)}"}


def test_evidence_download_works_without_tenant_header(db, client):
    tenant, _, emp, _, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    entry_id = r.json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    client.post(f"/api/daily-tasks/{entry_id}/evidence",
                files={"file": ("shot.png", png, "image/png")},
                headers=auth_headers(emp, tenant))
    # Exactly what a new-tab link / <img> / <iframe> sends: token, no header.
    r = client.get(f"/api/daily-tasks/{entry_id}/evidence", headers=_bearer_only(emp))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/png")
    assert r.content == png
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def test_evidence_download_without_tenant_header_still_scoped(db, client):
    from app.tests.factories import make_tenant

    tenant, _, emp, _, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    entry_id = r.json()["id"]
    client.post(f"/api/daily-tasks/{entry_id}/evidence",
                files={"file": ("shot.png", b"\x89PNG\r\n\x1a\n", "image/png")},
                headers=auth_headers(emp, tenant))
    # Another tenant's user, valid token, no header: must not see the entry.
    tenant2 = make_tenant(db, name="Other Co", slug="other-co")
    outsider = make_user(db, tenant2, UserRole.EMPLOYEE, name="Outsider2")
    r = client.get(f"/api/daily-tasks/{entry_id}/evidence", headers=_bearer_only(outsider))
    assert r.status_code == 404
    # No token at all: still 401.
    r = client.get(f"/api/daily-tasks/{entry_id}/evidence")
    assert r.status_code == 401
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def _make_entry_with_evidence(client, tenant, emp, filename, content, mime):
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]
    r = client.post(f"/api/daily-tasks/{entry_id}/evidence",
                    files={"file": (filename, content, mime)},
                    headers=auth_headers(emp, tenant))
    return entry_id, r


def test_evidence_rejects_html_svg_and_scripts(db, client):
    tenant, _, emp, _, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]
    for filename, content, mime in [
        ("evil.html", b"<script>alert(1)</script>", "text/html"),
        ("evil.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml"),
        ("evil.js", b"alert(1)", "application/javascript"),
    ]:
        r = client.post(f"/api/daily-tasks/{entry_id}/evidence",
                        files={"file": (filename, content, mime)},
                        headers=auth_headers(emp, tenant))
        assert r.status_code == 400, r.text
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def test_evidence_rejects_content_mismatch(db, client):
    tenant, _, emp, _, _ = _setup(db)
    entry_id, r = _make_entry_with_evidence(
        client, tenant, emp, "fake.png", b"this is not a png", "image/png")
    assert r.status_code == 400, r.text
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def test_evidence_office_doc_served_as_attachment(db, client):
    tenant, _, emp, _, _ = _setup(db)
    docx = b"PK\x03\x04" + b"\x00" * 32
    entry_id, r = _make_entry_with_evidence(
        client, tenant, emp, "report.docx", docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert r.status_code == 200, r.text
    r = client.get(f"/api/daily-tasks/{entry_id}/evidence", headers=auth_headers(emp, tenant))
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith("attachment")
    assert "wordprocessingml" in r.headers["content-type"]
    assert r.content == docx
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def test_evidence_image_served_inline_with_explicit_type(db, client):
    tenant, _, emp, _, _ = _setup(db)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    entry_id, r = _make_entry_with_evidence(client, tenant, emp, "shot.png", png, "image/png")
    assert r.status_code == 200, r.text
    r = client.get(f"/api/daily-tasks/{entry_id}/evidence", headers=auth_headers(emp, tenant))
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["content-type"].startswith("image/png")
    client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))


def test_delete_entry_removes_file_after_commit(db, client):
    from app.routers.daily_tasks import UPLOAD_DIR

    tenant, _, emp, _, _ = _setup(db)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    entry_id, r = _make_entry_with_evidence(client, tenant, emp, "shot.png", png, "image/png")
    assert r.status_code == 200, r.text
    stored = r.json()["evidence_file"]
    assert stored and (UPLOAD_DIR / stored).exists()
    r = client.delete(f"/api/daily-tasks/{entry_id}", headers=auth_headers(emp, tenant))
    assert r.status_code == 204, r.text
    assert not (UPLOAD_DIR / stored).exists()


def test_duplicate_entry_same_day_conflicts(db, client):
    tenant, _, emp, _, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    assert r.status_code == 201, r.text
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(emp, tenant))
    assert r.status_code == 409, r.text
    r = client.post("/api/daily-tasks", json=_entry_payload(task_date="2026-11-03"),
                    headers=auth_headers(emp, tenant))
    assert r.status_code == 201, r.text


def test_dept_admin_cannot_give_self_feedback(db, client):
    tenant, _, _, dept_admin, _ = _setup(db)
    r = client.post("/api/daily-tasks", json=_entry_payload(), headers=auth_headers(dept_admin, tenant))
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]
    r = client.post(f"/api/daily-tasks/{entry_id}/feedback",
                    json={"supervisor_feedback": "Great work, me!"},
                    headers=auth_headers(dept_admin, tenant))
    assert r.status_code == 403, r.text


def test_dept_admin_cannot_write_self_hod_evaluation(db, client):
    tenant, _, _, dept_admin, _ = _setup(db)
    r = client.put("/api/daily-tasks/review/month",
                    params={"year": 2026, "month": 11, "user_id": dept_admin.id},
                    json={"achievements": "Did things", "hod_evaluation": "Excellent, me!"},
                    headers=auth_headers(dept_admin, tenant))
    assert r.status_code == 200, r.text
    assert r.json()["achievements"] == "Did things"
    assert r.json()["hod_evaluation"] in (None, "")


def test_evening_reminder_skips_suspended_tenant(db):
    from app.models.tenant import TenantStatus

    tenant, _, emp, _, _ = _setup(db)
    tenant.status = TenantStatus.SUSPENDED
    db.commit()
    result = daily_task_reminder_service.send_daily_task_reminders(db, today=date(2026, 11, 2))
    assert emp.email not in result["reminded"]
    notes = db.scalars(
        select(Notification).where(
            Notification.user_id == emp.id, Notification.type == "daily_task_reminder"
        )
    ).all()
    assert notes == []
