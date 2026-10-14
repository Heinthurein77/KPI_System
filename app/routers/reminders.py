"""Manual trigger for deadline reminders (tenant admin).

The automatic path is app/jobs/send_deadline_reminders.py, run daily from the
server's cron. This endpoint lets a tenant admin fire the same logic on demand
for their own tenant.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_tenant_admin
from app.database import get_db
from app.models.user import User
from app.services import deadline_reminder_service

router = APIRouter(prefix="/api/admin/reminders", tags=["reminders"])


class DeadlineReminderOut(BaseModel):
    window_open: bool
    year: int
    period: str
    reminded: int
    already_reminded: int


@router.post("/deadline", response_model=DeadlineReminderOut, dependencies=[Depends(require_tenant_admin)])
def trigger_deadline_reminders(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Send deadline reminders to employees of the caller's tenant.

    Safe to call repeatedly: each user gets at most one reminder per period.
    Outside the reminder window (or when disabled) it does nothing and
    reports window_open=false.
    """
    result = deadline_reminder_service.send_deadline_reminders(db, tenant_id=user.tenant_id)
    return DeadlineReminderOut(
        window_open=result.window_open,
        year=result.year,
        period=result.period,
        reminded=result.reminded,
        already_reminded=result.already_reminded,
    )
