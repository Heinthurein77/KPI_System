"""Excel rendering for the attendance detail export.

Read-only: turns existing attendance records (one row per working day per
person) into a downloadable workbook. Follows the KPI export service's
styling (header fill, alternating rows, formula-injection guard) so the
attendance download looks like the KPI download.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

_HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
_SUMMARY_FILL = PatternFill("solid", fgColor="EAF2F8")
_ALTERNATE_FILL = PatternFill("solid", fgColor="F8FAFC")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_TITLE_FONT = Font(size=14, bold=True, color="1E3A5F")
_LABEL_FONT = Font(bold=True, color="334155")
_THIN_BORDER = Border(bottom=Side(style="thin", color="CBD5E1"))

HEADERS = [
    "Date",
    "Employee Name",
    "Department",
    "Role",
    "Check-in",
    "Check-out",
    "Status",
    "Late (min)",
]

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _safe(value: object) -> object:
    """Neutralize Excel formula injection: a string starting with =, +, - or @
    is treated as a formula by spreadsheet apps, so prefix it with a single
    quote to force plain-text interpretation."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def build_attendance_detail_report(
    entries: list[dict],
    year: int,
    month: int,
    department_label: str | None = None,
) -> BytesIO:
    """Return a formatted .xlsx with one row per working day per person.

    Each entry is a dict with keys: date (ISO "YYYY-MM-DD" string),
    employee_name, department_name, role, check_in, check_out, status,
    late_minutes. Entries are written in the order given (employee name,
    then date). When entries is empty only the title + header row are
    written (headers-only file).
    """
    workbook = Workbook()
    sheet: Worksheet = workbook.active
    sheet.title = "Attendance Detail"
    sheet.sheet_view.showGridLines = False

    sheet.merge_cells("A1:H1")
    sheet["A1"] = f"Attendance Detail — {MONTH_NAMES[month - 1]} {year}"
    sheet["A1"].font = _TITLE_FONT
    sheet["A1"].alignment = Alignment(horizontal="left")

    sheet.merge_cells("A2:H2")
    sheet["A2"] = f"Department: {department_label}" if department_label else "Department: All departments"
    sheet["A2"].font = _LABEL_FONT
    sheet["A2"].alignment = Alignment(horizontal="left")
    for row in sheet["A1:H2"]:
        for cell in row:
            cell.fill = _SUMMARY_FILL
            cell.alignment = Alignment(vertical="center")

    for column_number, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=4, column=column_number, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _THIN_BORDER

    for row_number, entry in enumerate(entries, start=5):
        values = [
            entry["date"],
            entry["employee_name"],
            entry["department_name"],
            entry["role"],
            entry["check_in"],
            entry["check_out"],
            entry["status"],
            entry["late_minutes"],
        ]
        for column_number, value in enumerate(values, start=1):
            sheet.cell(row=row_number, column=column_number, value=_safe(value))
        if row_number % 2 == 0:
            for cell in sheet[row_number]:
                cell.fill = _ALTERNATE_FILL
        for cell in sheet[row_number]:
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(vertical="center")

    sheet.freeze_panes = "A5"
    widths = {"A": 14, "B": 24, "C": 20, "D": 14, "E": 12, "F": 12, "G": 12, "H": 12}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
