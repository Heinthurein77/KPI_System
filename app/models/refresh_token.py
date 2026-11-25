"""Rotating refresh tokens (S-03, security review 2026-11-25).

Access tokens now live 1 hour; a session is kept alive by a refresh
token stored here, hashed (sha256 of the raw value — the raw value only
ever exists in the httpOnly cookie and in memory). Rotation: every
successful refresh deletes the used row and inserts a fresh one, so a
replayed/stolen refresh token finds no row and fails.

``token_version`` is snapshotted at issue time: a password change/reset
bumps the user's version, and a refresh whose snapshot no longer matches
is refused — so a credential change kills refresh tokens too, even when
the reset happened through a path that cannot delete these rows directly
(e.g. the admin password-reset endpoint).

New table — the coordinator consolidates the Alembic migration for this pass.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # sha256 hex of the raw token value (64 chars); unique so a token can
    # be looked up without scanning.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Snapshot of user.token_version at issue — see module docstring.
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Updated on every successful refresh; a refresh is refused when the
    # session has been idle longer than REFRESH_TOKEN_IDLE_TIMEOUT_HOURS.
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
