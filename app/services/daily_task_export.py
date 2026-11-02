"""Excel export for the Daily Task diary (pick 2).

Output mirrors the original HO_Working_Diary.xlsx layout: one diary sheet
per employee (title, instruction, header block, 11 columns) plus a Monthly
Dashboard sheet with per-user totals and the month-end review sections.
"""

from __future__ import annotations

import io
from datetime import date as date_type

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.daily_task import DailyTaskEntry, DailyTaskMonthlyReview
from app.models.department import Department
from app.models.user import User

DIARY_COLUMNS = [
    "Date\n(ရက်စွဲ)",
    "Plan Linkage\n(Plan ၏ အစိတ်အပိုင်း)",
    "Work Done\n(ယနေ့ လုပ်ခဲ့သော / ပြီးမြောက်သော အလုပ်)",
    "Status\n(အခြေအနေ)",
    "Figure / Output\n(ကိန်းဂဏန်း / ရလဒ်)",
    "Evidence\n(သက်သေ — Report/File/Link)",
    "Difficulties & Pending\n(အခက်အခဲ / မပြီးသေးသည့် အကြောင်းရင်း)",
    "Support Needed\n(လိုအပ်သော ပံ့ပိုးမှု)",
    "Self-Reflection\n(ကိုယ်တိုင် သုံးသပ်ချက်)",
    "Tomorrow Top 3 Plan\n(မနက်ဖြန် အဓိက အလုပ် ၃ ခု)",
    "Supervisor Feedback\n(အထက်လူကြီး မှတ်ချက်)",
]

STATUS_LABELS = {
    "not_started": "Not Started",
    "in_progress": "In Progress",
    "completed": "Completed",
    "pending": "Pending",
}


def _month_bounds(year: int, month: int) -> tuple[date_type, date_type]:
    start = date_type(year, month, 1)
    end = date_type(year + 1, 1, 1) if month == 12 else date_type(year, month + 1, 1)
    return start, end


def build_export(
    db: Session, tenant_id: int, users: list[User], year: int, month: int
) -> io.BytesIO:
    start, end = _month_bounds(year, month)
    wb = Workbook()
    wb.remove(wb.active)
    bold = Font(bold=True)

    dashboard_rows: list[list] = []
    for user in users:
        entries = db.scalars(
            select(DailyTaskEntry)
            .where(
                DailyTaskEntry.tenant_id == tenant_id,
                DailyTaskEntry.user_id == user.id,
                DailyTaskEntry.task_date >= start,
                DailyTaskEntry.task_date < end,
            )
            .order_by(DailyTaskEntry.task_date, DailyTaskEntry.id)
        ).all()
        dept_name = None
        if user.department_id:
            dept = db.get(Department, user.department_id)
            dept_name = dept.name if dept else None
        ws = wb.create_sheet(f"Diary - {user.name}"[:31])
        ws.append(["HEAD OFFICE - DAILY WORKING DIARY (နေ့စဉ် အလုပ် မှတ်တမ်း)"])
        ws["A1"].font = bold
        ws.append(["📌 နေ့စဉ် အလုပ်ပြီးချိန်တွင် ဖြည့်စွက်၍ HOD ထံ တင်ပြရန်။ တိုတိုရှင်းရှင်း၊ အချက်အလက် တိကျစွာ ရေးသားပါ။"])
        ws.append(["Employee Name (အမည်):", user.name, "", "", "", "Department (ဌာန):", dept_name or ""])
        ws.append(["Position (ရာထူး):", user.position or "", "", "", "", "Month / Year (လ / ခုနှစ်):", f"{year}-{month:02d}"])
        ws.append(DIARY_COLUMNS)
        for cell in ws[5]:
            cell.font = bold
        for e in entries:
            tomorrow = "\n".join(
                p for p in (e.tomorrow_plan_1, e.tomorrow_plan_2, e.tomorrow_plan_3) if p
            )
            evidence = e.evidence_link or ""
            if e.evidence_file:
                evidence = (evidence + " " if evidence else "") + f"[file: {e.evidence_file}]"
            ws.append([
                e.task_date.isoformat(),
                e.plan_linkage or "",
                e.work_done,
                STATUS_LABELS.get(e.status, e.status),
                e.figure_output or "",
                evidence,
                e.difficulties or "",
                e.support_needed or "",
                e.self_reflection or "",
                tomorrow,
                e.supervisor_feedback or "",
            ])
        for col in range(1, 12):
            ws.column_dimensions[get_column_letter(col)].width = 22
        ws.column_dimensions["C"].width = 45

        working_days = len({e.task_date for e in entries})
        completed = sum(1 for e in entries if e.status == "completed")
        in_prog = sum(1 for e in entries if e.status in ("in_progress", "pending"))
        review = db.scalars(
            select(DailyTaskMonthlyReview).where(
                DailyTaskMonthlyReview.tenant_id == tenant_id,
                DailyTaskMonthlyReview.user_id == user.id,
                DailyTaskMonthlyReview.year == year,
                DailyTaskMonthlyReview.month == month,
            )
        ).first()
        dashboard_rows.append([user.name, working_days, completed, in_prog])
        dashboard_rows.append(["Major Achievements", review.achievements if review else ""])
        dashboard_rows.append(["Key Challenges & Solutions", review.challenges_solutions if review else ""])
        dashboard_rows.append(["Areas for Improvement / Next Month Focus", review.improvements_next_focus if review else ""])
        dashboard_rows.append(["HOD Evaluation & Recommendations", review.hod_evaluation if review else ""])
        dashboard_rows.append([])

    ws = wb.create_sheet("Monthly Dashboard")
    ws.append(["MONTHLY WORK SUMMARY & KPI TRACKER (လစဉ် လုပ်ငန်းချုပ်နှင့် သုံးသပ်ချက်)"])
    ws["A1"].font = bold
    ws.append(["Employee", "Total Working Days (စုစုပေါင်း အလုပ်လုပ်ရက်)", "Completed Tasks (ပြီးစီးခဲ့သော အလုပ်များ)", "In Progress / Pending (မပြီးသေးသော အလုပ်များ)"])
    for cell in ws[2]:
        cell.font = bold
    for row in dashboard_rows:
        ws.append(row)
    for col in range(1, 5):
        ws.column_dimensions[get_column_letter(col)].width = 30

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out
