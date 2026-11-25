"""Security-review (2026-11-25) validation tests — Track 3.

Schema-boundary checks that need no database: M-32 (scores/comments
caps), M-33/M-34 (user name/email/position limits), M-35 (tenant slug
+ admin email), M-39 (control characters in names; metrics exempt), M-24/M-25
(office IPs + list caps), M-36 (zip-bomb guard), S-11 (lockfile
consistency), M-38 (shared year/period validator exists and is right).
"""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.validation import validate_year_period
from app.schemas.attendance import AttendanceSettingsUpdate
from app.schemas.kpi import SaveScoresRequest, SubmitRequest
from app.schemas.kpi_template import (
    CreateCustomTemplateRequest,
    CreateTemplateRequest,
    UpdateTemplateNameRequest,
)
from app.schemas.platform import CreateTenantRequest
from app.schemas.user import (
    CreateDepartmentRequest,
    CreateUserRequest,
    UpdateUserRequest,
)
from app.services.bulk_import_service import (
    MAX_UNCOMPRESSED_BYTES,
    _check_uncompressed_size,
    parse_import_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_user(**overrides):
    data = {
        "name": "Aung Aung",
        "email": "aung@example.com",
        "password": "Password123!",
        "role": "employee",
    }
    data.update(overrides)
    return data


# ------------------------------------------------------------------ M-33


def test_create_user_rejects_too_long_name():
    with pytest.raises(ValidationError):
        CreateUserRequest(**_valid_user(name="x" * 121))


def test_create_user_rejects_too_long_email():
    with pytest.raises(ValidationError):
        CreateUserRequest(**_valid_user(email="x" * 250 + "@example.com"))


def test_create_user_rejects_too_long_position():
    with pytest.raises(ValidationError):
        CreateUserRequest(**_valid_user(position="x" * 121))


def test_update_user_rejects_too_long_name():
    with pytest.raises(ValidationError):
        UpdateUserRequest(
            name="x" * 121, email="a@example.com", role="employee"
        )


def test_create_department_rejects_too_long_name():
    with pytest.raises(ValidationError):
        CreateDepartmentRequest(name="x" * 121)


def test_create_user_at_the_limits_is_accepted():
    req = CreateUserRequest(
        **_valid_user(name="x" * 120, position="y" * 120)
    )
    assert len(req.name) == 120
    assert len(req.position) == 120


# ------------------------------------------------------------------ M-34


def test_update_user_rejects_blank_name():
    with pytest.raises(ValidationError):
        UpdateUserRequest(name="   ", email="a@example.com", role="employee")


def test_update_user_blank_position_becomes_none():
    req = UpdateUserRequest(
        name="Aung", email="a@example.com", role="employee", position="   "
    )
    assert req.position is None


def test_create_user_blank_position_becomes_none():
    req = CreateUserRequest(**_valid_user(position="  "))
    assert req.position is None


# ------------------------------------------------------------------ M-35


def test_create_tenant_rejects_bad_slugs():
    for slug in ("a b", "a/b", "", "-lead"):
        with pytest.raises(ValidationError, match="Slug"):
            CreateTenantRequest(
                name="Acme", slug=slug, admin_name="Admin",
                admin_email="admin@example.com", admin_password="Password123!",
            )


def test_create_tenant_slug_is_lowercased_and_validated():
    req = CreateTenantRequest(
        name="Acme", slug="ACME-1", admin_name="Admin",
        admin_email="admin@example.com", admin_password="Password123!",
    )
    assert req.slug == "acme-1"


def test_create_tenant_rejects_bad_admin_email():
    with pytest.raises(ValidationError):
        CreateTenantRequest(
            name="Acme", slug="acme", admin_name="Admin",
            admin_email="not-an-email", admin_password="Password123!",
        )


def test_create_tenant_rejects_too_long_name_and_slug():
    with pytest.raises(ValidationError):
        CreateTenantRequest(
            name="x" * 201, slug="acme", admin_name="Admin",
            admin_email="a@example.com", admin_password="Password123!",
        )
    with pytest.raises(ValidationError):
        CreateTenantRequest(
            name="Acme", slug="x" * 101, admin_name="Admin",
            admin_email="a@example.com", admin_password="Password123!",
        )


# ------------------------------------------------------------------ M-39


@pytest.mark.parametrize("bad", ["Aung\nAung", "Aung\rAung", "Aung\tAung"])
def test_user_name_rejects_control_chars(bad):
    with pytest.raises(ValidationError):
        CreateUserRequest(**_valid_user(name=bad))
    with pytest.raises(ValidationError):
        UpdateUserRequest(name=bad, email="a@example.com", role="employee")


@pytest.mark.parametrize("bad", ["Sales\nLead", "Sales\tLead"])
def test_user_position_rejects_control_chars(bad):
    with pytest.raises(ValidationError):
        CreateUserRequest(**_valid_user(position=bad))


def test_metric_name_with_control_chars_accepted_as_is():
    # M-39 follow-up (2026-11-25): metric names are deliberately EXEMPT
    # from the control-character rejection — live production metrics
    # contain newlines (the ~245-char Audit Cycle metric) and the user
    # rule is metric names entered as-is; test_long_metric_custom_create_
    # keeps_full_audit_label pins the end-to-end behaviour. The
    # newline-in-email-Subject risk is a residual for the email layer.
    assert CreateTemplateRequest(
        metric_name="Sales\nTarget", target=100, weight=50
    ).metric_name == "Sales\nTarget"
    assert UpdateTemplateNameRequest(
        metric_name="Sales\rTarget"
    ).metric_name == "Sales\rTarget"
    req = CreateCustomTemplateRequest(
        employee_id=1, metric_name="Bad\tMetric", target=100,
        weight=50, year=2026, period="November",
    )
    assert req.metric_name == "Bad\tMetric"


def test_metric_name_blank_still_rejected():
    with pytest.raises(ValidationError):
        CreateTemplateRequest(metric_name="   ", target=100, weight=50)


def test_department_name_rejects_control_chars():
    with pytest.raises(ValidationError):
        CreateDepartmentRequest(name="Sales\nDept")


# ------------------------------------------------------------------ M-32


def test_save_scores_rejects_over_500_entries():
    with pytest.raises(ValidationError):
        SaveScoresRequest(
            year=2026, period="November",
            scores={str(i): 1.0 for i in range(501)},
        )
    with pytest.raises(ValidationError):
        SubmitRequest(
            year=2026, period="November",
            comments={str(i): "ok" for i in range(501)},
        )


def test_save_scores_accepts_500_entries():
    req = SaveScoresRequest(
        year=2026, period="November",
        scores={str(i): 1.0 for i in range(500)},
    )
    assert len(req.scores) == 500


# ------------------------------------------------------------------ M-24


@pytest.mark.parametrize(
    "bad_ip",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1",
     "fd00::1", "::1", "224.0.0.1", "100.64.0.1", "0.0.0.0"],
)
def test_office_ips_reject_never_matchable_addresses(bad_ip):
    with pytest.raises(ValidationError):
        AttendanceSettingsUpdate(office_ips=[bad_ip])


@pytest.mark.parametrize("good_ip", ["8.8.8.8", "203.0.113.10", "1.1.1.1"])
def test_office_ips_accept_public_addresses(good_ip):
    # 203.0.113.10 is TEST-NET-3: is_global is False for it, but it is
    # the office IP the test suite uses as a stand-in, so it is allowed.
    req = AttendanceSettingsUpdate(office_ips=[good_ip])
    assert req.office_ips == [good_ip]


# ------------------------------------------------------------------ M-25


def test_office_ips_capped_at_20():
    with pytest.raises(ValidationError):
        AttendanceSettingsUpdate(office_ips=[f"8.8.8.{i}" for i in range(1, 22)])


def test_holidays_capped_at_366():
    with pytest.raises(ValidationError):
        AttendanceSettingsUpdate(holidays=["2026-01-01"] * 367)


# ------------------------------------------------------------------ M-36


def _zip_with_entry(name: str, size: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Highly compressible content: a 60 MB entry is a ~60 KB zip.
        zf.writestr(name, b"\0" * size)
    return buf.getvalue()


def test_uncompressed_size_check_rejects_zip_bomb():
    bomb = _zip_with_entry("xl/worksheets/sheet1.xml", MAX_UNCOMPRESSED_BYTES + 1)
    assert len(bomb) < 1024 * 1024  # small compressed, huge uncompressed
    assert _check_uncompressed_size(bomb) is not None


def test_uncompressed_size_check_rejects_oversized_single_entry():
    bomb = _zip_with_entry("big.bin", MAX_UNCOMPRESSED_BYTES + 1)
    assert _check_uncompressed_size(bomb) is not None


def test_uncompressed_size_check_accepts_small_workbook():
    from app.services.bulk_import_service import build_import_template

    assert _check_uncompressed_size(build_import_template().getvalue()) is None


def test_parse_import_file_rejects_zip_bomb(db):
    from app.tests.factories import make_tenant

    tenant = make_tenant(db)
    bomb = _zip_with_entry("xl/worksheets/sheet1.xml", MAX_UNCOMPRESSED_BYTES + 1)
    parsed = parse_import_file(bomb, db, tenant.id)
    assert not parsed.ok
    assert any("decompressed" in e.message for e in parsed.errors)


def test_parse_import_file_rejects_control_chars_in_name(db):
    from openpyxl import Workbook

    from app.tests.factories import make_tenant

    tenant = make_tenant(db)
    wb = Workbook()
    ws = wb.active
    ws.title = "Import"
    ws.append(["name", "email", "position", "metric", "target", "weight"])
    ws.append(["Bad\nName", "bad@example.com", "", "Sales", 100, 100])
    buf = io.BytesIO()
    wb.save(buf)
    parsed = parse_import_file(buf.getvalue(), db, tenant.id)
    assert not parsed.ok
    assert any("line breaks" in e.message for e in parsed.errors)


# ------------------------------------------------------------------ S-11


def test_requirements_lock_matches_pinned_requirements():
    """Every == pin in requirements.txt must appear at the same version
    in requirements.lock, and the lockfile must contain no ranges."""
    import re

    def _pins(path: Path) -> dict[str, str]:
        pins = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]+\])?==(.+)$", line)
            assert m, f"unpinned or unparsable requirement: {line!r}"
            pins[m.group(1).lower()] = m.group(3).strip()
        return pins

    req_pins = _pins(REPO_ROOT / "requirements.txt")
    assert req_pins, "requirements.txt has no pins"
    lock_pins = _pins(REPO_ROOT / "requirements.lock")
    for name, version in req_pins.items():
        assert lock_pins.get(name) == version, (
            f"{name}=={version} in requirements.txt but "
            f"{lock_pins.get(name)} in requirements.lock"
        )
    for line in (REPO_ROOT / "requirements.lock").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            assert ">=" not in line and "<" not in line, line


# ------------------------------------------------------------------ M-38


def test_shared_year_period_validator_rejects_out_of_range():
    with pytest.raises(HTTPException):
        validate_year_period(1999, "November")
    with pytest.raises(HTTPException):
        validate_year_period(2101, "November")
    with pytest.raises(HTTPException):
        validate_year_period(2026, "NotAMonth")
    assert validate_year_period(2026, "November") == (2026, "November")
