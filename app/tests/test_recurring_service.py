"""Coverage for the opt-in monthly carry-over extension."""

from app.models.kpi_submission import KPIStatus, KPISubmission
from app.models.user import UserRole
from app.services import kpi_service, recurring_service
from app.tests.factories import make_department, make_template, make_tenant, make_user


def test_recurring_run_creates_one_draft_and_is_idempotent(db):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    template = make_template(
        db,
        tenant,
        employee=employee,
        department=department,
        locked_year=2026,
        locked_period="March",
        is_recurring=True,
    )

    first = recurring_service.materialise_recurring_for_period(db, 2026, "April", tenant.id)
    second = recurring_service.materialise_recurring_for_period(db, 2026, "April", tenant.id)

    assert first.created_count == 1
    assert second.created_count == 0
    assert second.skipped_count == 1
    rows = db.query(KPISubmission).filter_by(kpi_template_id=template.id, year=2026, month_or_quarter="April").all()
    assert len(rows) == 1
    assert rows[0].status == KPIStatus.DRAFT
    assert rows[0].self_score is None


def test_recurring_run_never_backfills_before_template_start(db):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    template = make_template(
        db,
        tenant,
        employee=employee,
        department=department,
        locked_year=2026,
        locked_period="May",
        is_recurring=True,
    )

    result = recurring_service.materialise_recurring_for_period(db, 2026, "April", tenant.id)

    assert result.created_count == 0
    assert db.query(KPISubmission).filter_by(kpi_template_id=template.id).count() == 0


def test_dashboard_materialisation_carries_recurring_custom_template_forward(db):
    tenant = make_tenant(db)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    template = make_template(
        db,
        tenant,
        employee=employee,
        department=department,
        locked_year=2026,
        locked_period="May",
        is_recurring=True,
    )

    submissions = kpi_service.ensure_period_submissions(db, employee, 2026, "June")

    assert [submission.kpi_template_id for submission in submissions] == [template.id]
    assert submissions[0].status == KPIStatus.DRAFT


def test_recurring_run_endpoint_is_tenant_scoped_and_idempotent(db, client):
    from app.tests.factories import auth_headers

    tenant = make_tenant(db)
    admin = make_user(db, tenant, UserRole.TENANT_ADMIN)
    department = make_department(db, tenant)
    employee = make_user(db, tenant, UserRole.EMPLOYEE, department=department)
    make_template(
        db,
        tenant,
        employee=employee,
        department=department,
        locked_year=2026,
        locked_period="March",
        is_recurring=True,
    )

    payload = {"year": 2026, "period": "April"}
    first = client.post("/api/admin/recurring/run", json=payload, headers=auth_headers(admin, tenant))
    second = client.post("/api/admin/recurring/run", json=payload, headers=auth_headers(admin, tenant))

    assert first.status_code == 200
    assert first.json()["created_count"] == 1
    assert second.status_code == 200
    assert second.json()["created_count"] == 0
    assert second.json()["skipped_count"] == 1
