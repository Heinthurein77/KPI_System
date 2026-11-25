"""M-41 (security review 2026-11-25): the seeded super admin must change
its password on first login, and the password is never printed to stdout.
"""

import importlib
import re

from app.models.user import User, UserRole


def _reload_seed_with_env(monkeypatch, env):
    """Re-import app.seed with the given env vars set (it reads them at
    import time), returning the module."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.seed

    return importlib.reload(app.seed)


def test_seed_sets_must_change_password_and_never_prints_it(db, capsys, monkeypatch):
    seed = _reload_seed_with_env(
        monkeypatch,
        {
            "PLATFORM_SUPER_ADMIN_NAME": "Seeded Root",
            "PLATFORM_SUPER_ADMIN_EMAIL": "seeded-root@example.com",
            "PLATFORM_SUPER_ADMIN_PASSWORD": "SeededPass123!",
        },
    )
    seed.run()
    out = capsys.readouterr().out

    assert "SeededPass123!" not in out
    assert "seeded-root@example.com" in out

    user = db.query(User).filter(User.tenant_id.is_(None)).one()
    assert user.role == UserRole.SUPER_ADMIN
    assert user.must_change_password is True
    from app.core.security import verify_password

    assert verify_password("SeededPass123!", user.password_hash)

    # Second run is a no-op (an admin already exists).
    seed.run()
    assert "already exists" in capsys.readouterr().out
    assert db.query(User).filter(User.tenant_id.is_(None)).count() == 1


def test_seed_generated_password_goes_to_a_file_not_stdout(db, capsys, monkeypatch):
    from pathlib import Path

    monkeypatch.delenv("PLATFORM_SUPER_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("PLATFORM_SUPER_ADMIN_EMAIL", "generated-root@example.com")
    import app.seed

    seed = importlib.reload(app.seed)
    assert seed._generated_password is True
    seed.run()
    out = capsys.readouterr().out

    user = db.query(User).filter(User.tenant_id.is_(None)).one()
    assert user.must_change_password is True

    # The printed line carries the file's absolute path, never the secret.
    match = re.search(r"(/[^ \n]*platform-admin-password-\S+)", out)
    assert match, out
    path = Path(match.group(1))
    assert path.exists()
    # The file is mode 0600 and holds the password; stdout does not.
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert path.read_text() not in out
    assert len(path.read_text()) > 20
    path.unlink(missing_ok=True)
