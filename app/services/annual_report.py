"""Annual KPI appraisal report: data gathering + PDF generation.

Read-only over existing submissions — reuses the effective_score /
effective_target properties and the same attainment convention as the
frontend (score/target*100 capped at 120). No calculation code is changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from pathlib import Path

from fpdf import FPDF
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.department import Department
from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.user import User

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

ATTAINMENT_CAP = 120.0


def attainment_pct(score: float | None, target: float | None) -> float | None:
    """Attainment % for one submission, same convention as the frontend ring."""
    if score is None or not target:
        return None
    return min(score / target * 100.0, ATTAINMENT_CAP)


@dataclass
class MonthRow:
    year: int
    period: str
    metric_count: int
    avg_attainment: float | None


@dataclass
class MetricRow:
    metric_name: str
    target: float | None
    avg_score: float | None
    avg_attainment: float | None
    months_count: int


@dataclass
class AnnualReport:
    employee_name: str
    department_name: str
    position: str | None
    year: int
    months: list[MonthRow] = field(default_factory=list)
    metrics: list[MetricRow] = field(default_factory=list)
    remarks: list[str] = field(default_factory=list)
    yearly_avg_attainment: float | None = None
    months_with_data: int = 0
    best_month: str | None = None


def build_annual_report(db: Session, tenant_id: int, employee_id: int, year: int) -> AnnualReport:
    employee = db.scalar(
        select(User).where(User.id == employee_id, User.tenant_id == tenant_id)
    )
    if employee is None:
        raise LookupError("Employee not found")
    department = (
        db.scalar(select(Department).where(Department.id == employee.department_id))
        if employee.department_id
        else None
    )

    report = AnnualReport(
        employee_name=employee.name,
        department_name=department.name if department else "-",
        position=employee.position,
        year=year,
    )

    # metric_name -> list of (score, attainment, target)
    by_metric: dict[str, list[tuple[float, float | None, float | None]]] = {}
    remarks: list[tuple[date | None, str]] = []

    for m in range(1, 13):
        period = MONTH_NAMES[m - 1]
        submissions = db.scalars(
            select(KPISubmission)
            .options(joinedload(KPISubmission.kpi_template))
            .where(
                KPISubmission.tenant_id == tenant_id,
                KPISubmission.employee_id == employee_id,
                KPISubmission.year == year,
                KPISubmission.month_or_quarter == period,
            )
        ).unique().all()

        attainments: list[float] = []
        for s in submissions:
            if s.status != KPIStatus.APPROVED:
                continue
            score = s.effective_score
            target = s.effective_target
            att = attainment_pct(score, target)
            if att is not None:
                attainments.append(att)
            if s.kpi_template and score is not None:
                by_metric.setdefault(s.kpi_template.metric_name, []).append(
                    (score, att, s.effective_target)
                )
            if s.remarks:
                when = s.final_reviewed_at.date() if s.final_reviewed_at else None
                remarks.append((when, s.remarks))

        avg = round(sum(attainments) / len(attainments), 1) if attainments else None
        report.months.append(MonthRow(
            year=year, period=period,
            metric_count=len(submissions), avg_attainment=avg,
        ))

    monthly_avgs = [r.avg_attainment for r in report.months if r.avg_attainment is not None]
    if monthly_avgs:
        report.yearly_avg_attainment = round(sum(monthly_avgs) / len(monthly_avgs), 1)
        report.months_with_data = len(monthly_avgs)
        best = max(
            (r for r in report.months if r.avg_attainment is not None),
            key=lambda r: r.avg_attainment,  # type: ignore[union-attr]
        )
        report.best_month = f"{best.period} ({best.avg_attainment}%)"

    for metric_name, pairs in sorted(by_metric.items()):
        scores = [p[0] for p in pairs]
        atts = [p[1] for p in pairs if p[1] is not None]
        targets = [p[2] for p in pairs if p[2]]
        target = max(set(targets), key=targets.count) if targets else None
        report.metrics.append(MetricRow(
            metric_name=metric_name,
            target=target,
            avg_score=round(sum(scores) / len(scores), 1) if scores else None,
            avg_attainment=round(sum(atts) / len(atts), 1) if atts else None,
            months_count=len(pairs),
        ))

    remarks.sort(key=lambda r: r[0] or date.min, reverse=True)
    report.remarks = [text for _, text in remarks[:10]]
    return report


class _ReportPDF(FPDF):
    def header(self):
        self.set_font("NotoMM", "B", 11)
        self.set_text_color(60, 60, 60)
        self.cell(0, 8, "Annual KPI Appraisal Report", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("NotoMM", "", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, text: str):
        self.set_font("NotoMM", "B", 12)
        self.set_text_color(30, 30, 30)
        self.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def kv(self, label: str, value: str):
        self.set_font("NotoMM", "B", 10)
        self.set_text_color(80, 80, 80)
        self.cell(38, 7, label)
        self.set_font("NotoMM", "", 10)
        self.set_text_color(20, 20, 20)
        self.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")


def _fmt(v: float | None, suffix: str = "") -> str:
    return f"{v}{suffix}" if v is not None else "-"


def render_pdf(report: AnnualReport) -> bytes:
    pdf = _ReportPDF(format="A4")
    pdf.alias_nb_pages()
    pdf.add_font("NotoMM", "", str(FONT_DIR / "NotoSansMyanmar-Regular.ttf"))
    pdf.add_font("NotoMM", "B", str(FONT_DIR / "NotoSansMyanmar-Bold.ttf"))
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()

    pdf.set_font("NotoMM", "B", 18)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 12, f"Annual KPI Report \u2014 {report.year}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.kv("Employee:", report.employee_name)
    pdf.kv("Department:", report.department_name)
    pdf.kv("Position:", report.position or "-")
    pdf.kv("Generated:", date.today().isoformat())
    pdf.ln(4)

    # Summary
    pdf.section_title("Yearly Summary")
    pdf.kv("Avg attainment:", _fmt(report.yearly_avg_attainment, "%"))
    pdf.kv("Months with data:", str(report.months_with_data))
    pdf.kv("Best month:", report.best_month or "-")
    pdf.ln(4)

    # Monthly breakdown
    pdf.section_title("Monthly Breakdown")
    pdf.set_font("NotoMM", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    col_w = [55, 45, 55]
    for w, h in zip(col_w, ("Month", "Metrics", "Avg attainment")):
        pdf.cell(w, 8, h, border=1, fill=True)
    pdf.ln()
    pdf.set_font("NotoMM", "", 10)
    for r in report.months:
        pdf.cell(col_w[0], 7, r.period, border=1)
        pdf.cell(col_w[1], 7, str(r.metric_count), border=1)
        pdf.cell(col_w[2], 7, _fmt(r.avg_attainment, "%"), border=1)
        pdf.ln()
    pdf.ln(4)

    # Per-metric performance
    if report.metrics:
        pdf.section_title("Performance by Metric")
        pdf.set_font("NotoMM", "B", 10)
        col_w = [70, 30, 30, 40]
        for w, h in zip(col_w, ("Metric", "Target", "Avg score", "Avg attainment")):
            pdf.cell(w, 8, h, border=1, fill=True)
        pdf.ln()
        pdf.set_font("NotoMM", "", 10)
        for m in report.metrics:
            pdf.cell(col_w[0], 7, m.metric_name[:38], border=1)
            pdf.cell(col_w[1], 7, _fmt(m.target), border=1)
            pdf.cell(col_w[2], 7, _fmt(m.avg_score), border=1)
            pdf.cell(col_w[3], 7, _fmt(m.avg_attainment, "%"), border=1)
            pdf.ln()
        pdf.ln(4)

    # Reviewer remarks
    if report.remarks:
        pdf.section_title("Reviewer Remarks (latest)")
        pdf.set_font("NotoMM", "", 10)
        for text in report.remarks:
            pdf.multi_cell(0, 6, f"\u2022 {text}")
            pdf.ln(1)

    out = pdf.output()
    return bytes(out)
