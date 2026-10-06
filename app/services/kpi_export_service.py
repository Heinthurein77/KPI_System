"""Excel rendering for individual monthly KPI reports.

This service is deliberately read-only: it turns existing submission records
and the already-calculated KPI summary into a downloadable workbook without
changing scores, templates, workflows, or database data.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.worksheet import Worksheet

from app.models.kpi_submission import KPISubmission
from app.models.user import User


_HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
_SUMMARY_FILL = PatternFill("solid", fgColor="EAF2F8")
_ALTERNATE_FILL = PatternFill("solid", fgColor="F8FAFC")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_TITLE_FONT = Font(size=14, bold=True, color="1E3A5F")
_LABEL_FONT = Font(bold=True, color="334155")
_THIN_BORDER = Border(bottom=Side(style="thin", color="CBD5E1"))


def _write_summary_cell(sheet: Worksheet, label_cell: str, value_cell: str, label: str, value: object) -> None:
    sheet[label_cell] = label
    sheet[label_cell].font = _LABEL_FONT
    sheet[value_cell] = value


def build_employee_kpi_report(
    employee: User,
    submissions: list[KPISubmission],
    year: int,
    month: str,
    combined_score: dict | None,
) -> BytesIO:
    """Return a formatted .xlsx report for one employee and monthly period."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "KPI Report"
    sheet.sheet_view.showGridLines = False

    sheet.merge_cells("A1:H1")
    sheet["A1"] = "Employee KPI Score Report"
    sheet["A1"].font = _TITLE_FONT
    sheet["A1"].alignment = Alignment(horizontal="left")

    _write_summary_cell(sheet, "A3", "B3", "Employee", employee.name)
    _write_summary_cell(sheet, "A4", "B4", "Email", employee.email)
    _write_summary_cell(sheet, "A5", "B5", "Department", employee.department.name if employee.department else "—")
    _write_summary_cell(sheet, "D3", "E3", "Month", month)
    _write_summary_cell(sheet, "D4", "E4", "Year", year)

    total_weight = sum(float(submission.kpi_template.weight) for submission in submissions)
    final_attainment = combined_score["attainment"] if combined_score else "N/A"
    final_status = combined_score["status"] if combined_score else "Not finalized"
    _write_summary_cell(sheet, "D5", "E5", "Total Weight", total_weight)
    _write_summary_cell(sheet, "G3", "H3", "Final KPI Score", final_attainment)
    _write_summary_cell(sheet, "G4", "H4", "KPI Status", final_status)
    sheet["E5"].number_format = "0.00"
    if isinstance(final_attainment, (int, float)):
        sheet["H3"].number_format = "0.00"

    headers = [
        "KPI Metric",
        "Weight",
        "Target",
        "Self Score",
        "Dept Score",
        "Final Score",
        "Actual Score",
        "Status",
    ]
    for column_number, header in enumerate(headers, start=1):
        cell = sheet.cell(row=7, column=column_number, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _THIN_BORDER

    for row_number, submission in enumerate(submissions, start=8):
        actual_score = submission.effective_score
        values = [
            submission.kpi_template.metric_name,
            float(submission.kpi_template.weight),
            float(submission.kpi_template.target),
            submission.self_score,
            submission.dept_score,
            submission.final_score,
            actual_score,
            submission.status.value,
        ]
        for column_number, value in enumerate(values, start=1):
            sheet.cell(row=row_number, column=column_number, value=value)
        if row_number % 2 == 0:
            for cell in sheet[row_number]:
                cell.fill = _ALTERNATE_FILL
        for cell in sheet[row_number]:
            cell.border = _THIN_BORDER
            cell.alignment = Alignment(vertical="center")
        for column in "BCDEFG":
            sheet[f"{column}{row_number}"].number_format = "0.00"

    summary_range = sheet["A3:H5"]
    for row in summary_range:
        for cell in row:
            cell.fill = _SUMMARY_FILL
            cell.alignment = Alignment(vertical="center")

    sheet.freeze_panes = "A8"
    widths = {"A": 34, "B": 12, "C": 12, "D": 14, "E": 14, "F": 14, "G": 14, "H": 24}
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
