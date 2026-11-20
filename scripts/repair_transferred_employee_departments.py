"""One-off repair for dept-transfer staleness (B-M5, senior review 2026-11-20).

Before the fix, ``edit_user`` changed only ``users.department_id`` when an
employee moved departments. ``kpi_submissions.department_id`` and
``attendance_records.department_id`` are denormalised from the employee at
creation, so rows written before a transfer kept the OLD department: the
old dept admin could still see/approve them, and the new dept admin could
not. The endpoint now updates both tables in the same transaction — but
rows already stale from transfers before that fix stay stale until
repaired. This script repairs them.

Usage (from the repo root, against the SERVER database)::

    DATABASE_URL=<server url> python scripts/repair_transferred_employee_departments.py [--apply]

- Default is DRY RUN: it only reports, per employee, how many KPI
  submission rows and attendance rows disagree with the employee's
  current department. Nothing is written.
- With ``--apply`` it updates the stale rows to the employee's current
  department (NULL when the employee is a tenant admin, matching what
  the fixed endpoint writes) and prints the counts it changed.

Safety: the script refuses to run unless DATABASE_URL is set explicitly
(never guess a database), prints the target database host before doing
anything, and with --apply it asks for confirmation on a TTY. Run it
once after deploying the B-M5 fix; re-running it afterwards should
report zero stale rows. It was NOT run against any real database as
part of the fix — the operator runs it on the server.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _target_label(url: str) -> str:
    # Host (and database name) only — never print credentials.
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname or '?'}{parsed.path or ''}"
    except Exception:
        return "(unparseable DATABASE_URL)"


def run(*, apply: bool) -> dict[str, int]:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models.attendance import AttendanceRecord
    from app.models.kpi_submission import KPISubmission
    from app.models.user import User

    db = SessionLocal()
    try:
        users = db.scalars(select(User)).all()
        stale_kpi = stale_att = employees_affected = 0
        for user in users:
            kpi_rows = db.scalars(
                select(KPISubmission).where(
                    KPISubmission.employee_id == user.id,
                    KPISubmission.department_id.is_distinct_from(user.department_id),
                )
            ).all()
            att_rows = db.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.employee_id == user.id,
                    AttendanceRecord.department_id.is_distinct_from(user.department_id),
                )
            ).all()
            if not kpi_rows and not att_rows:
                continue
            employees_affected += 1
            print(
                f"user {user.id} ({user.name}, {user.email}): "
                f"department_id={user.department_id}, "
                f"stale KPI rows={len(kpi_rows)}, stale attendance rows={len(att_rows)}"
            )
            stale_kpi += len(kpi_rows)
            stale_att += len(att_rows)
            if apply:
                for row in kpi_rows:
                    row.department_id = user.department_id
                for row in att_rows:
                    row.department_id = user.department_id
        if apply:
            db.commit()
        return {
            "employees_affected": employees_affected,
            "stale_kpi_rows": stale_kpi,
            "stale_attendance_rows": stale_att,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update the stale rows (default is dry-run report only).",
    )
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Refusing to run: set DATABASE_URL explicitly (never run against a guessed database).")
    print(f"Target database: {_target_label(url)}")
    if args.apply:
        if sys.stdin.isatty():
            answer = input("Update stale department_id rows to each employee's current department? [y/N] ")
            if answer.strip().lower() != "y":
                raise SystemExit("Aborted.")
        print("Applying repair...")
    else:
        print("Dry run — nothing will be written. Pass --apply to repair.")
    result = run(apply=args.apply)
    print(
        f"Done: {result['employees_affected']} employee(s) affected, "
        f"{result['stale_kpi_rows']} stale KPI submission row(s), "
        f"{result['stale_attendance_rows']} stale attendance row(s)"
        + (" updated." if args.apply else " (dry run — not updated).")
    )


if __name__ == "__main__":
    main()
