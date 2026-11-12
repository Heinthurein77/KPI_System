# Second Platform Admin — Mechanism (Investigation)

**Status:** investigation only, 2026-11-12 (HEAD `a2acb06`). Review finding M6.
**Scope:** how a second platform admin is created. Nothing was created, no
password was changed, no credential was touched — this document is the
prepared steps, not an execution record.

## Bottom line

**There is no way to create a second platform admin from inside the app.**
Not from the Platform portal, not from any API endpoint, not from the
tenant-admin UI. It needs **server access** — an SSH shell on the VPS and
one command inside the `web` container. The user holds the only server SSH
access, so this is the user's step (or someone the user lets onto the
server); the agent cannot do it from here.

## How the first admin came to exist

- The only creation path in the codebase is `app/seed.py`, run once as
  `docker compose exec web python -m app.seed`. It reads the bootstrap
  identity from env vars (`PLATFORM_SUPER_ADMIN_NAME`,
  `PLATFORM_SUPER_ADMIN_EMAIL`, `PLATFORM_SUPER_ADMIN_PASSWORD`) and
  inserts one `User` row with `tenant_id=None`, `role=SUPER_ADMIN`.
- **Re-running the seed does NOT create a second admin.** `run()` checks
  for *any* tenant-less user first (`select(User).where
  (User.tenant_id.is_(None)).limit(1)`) and prints "A platform Super Admin
  already exists — skipping seed." That guard is what makes the seed safe
  to re-run — and what blocks using it for admin number two.
- History: the pre-multi-tenancy bootstrap account (`admin@kpi.com`) was
  converted to the Default Organization's Tenant Admin by migration
  `0004_reassign_bootstrap_admin`; the current platform admin was then
  provisioned separately via the seed. The migration deliberately does not
  create platform accounts (no bootstrap password baked into history).

## Why no in-app path exists

| Path | Result |
|---|---|
| Platform portal (`/platform/...`) | No user-create endpoint at all — the platform router only manages tenants (`list/create/suspend/activate/delete`) plus its own login/me/change-password. |
| Tenant admin "Add User" (`POST /api/admin/users`) | Explicitly rejects the role: `admin.py` raises 400 "Cannot assign the platform Super Admin role to a tenant user." The same guard exists on user edit. A tenant admin can only ever create tenant-scoped users. |
| Tenant creation (`POST /api/platform/tenants`) | Creates a `TENANT_ADMIN` for the new tenant, never a platform admin. |
| `app/main.py` startup | Runs migrations only; the seed is never invoked at startup. |

So the "create a second admin" capability simply does not exist in the
running system. That is by design (platform admins are provisioned
out-of-band), and adding an in-app endpoint for it would be a new
privileged surface — a product/security decision, not something to slip
into a fix pass.

## The mechanism: one-off insert from the server shell

Who does what, in order:

1. **User (server SSH):** SSH to the VPS and go to the deploy checkout
   (`~/kpi-app` — adjust if the checkout lives elsewhere on the box).
2. **User:** pick the second admin's name, email, and a temporary
   password. The email **must differ** from the first admin's: platform
   emails are unique via a partial unique index (`uq_users_platform_email`,
   on `email` where `tenant_id IS NULL` — `app/models/user.py`).
3. **User:** run the insert inside the `web` container. The heredoc goes
   through `python -` so the app's own `hash_password` produces the hash
   (do not hand-craft a bcrypt hash, and do not use raw SQL — the Python
   route keeps the hash format correct by construction). The temporary
   password is passed via environment, never written into a file or into
   shell history as a literal:

   ```bash
   read -rsp "Temporary password for the second admin: " TEMP_PLATFORM_ADMIN_PASSWORD; echo
   docker compose exec -T -e TEMP_PLATFORM_ADMIN_PASSWORD="$TEMP_PLATFORM_ADMIN_PASSWORD" web python - <<'EOF'
   import os
   from app.core.security import hash_password
   from app.database import SessionLocal
   from app.models.user import User, UserRole

   email = "second-admin@example.com"  # <-- change to the real address
   db = SessionLocal()
   db.add(User(
       tenant_id=None,
       name="Second Platform Admin",  # <-- change to the real name
       email=email,
       password_hash=hash_password(os.environ["TEMP_PLATFORM_ADMIN_PASSWORD"]),
       role=UserRole.SUPER_ADMIN,
       department_id=None,
       must_change_password=True,
   ))
   db.commit()
   print(f"Second platform admin created: {email}")
   EOF
   unset TEMP_PLATFORM_ADMIN_PASSWORD
   ```

   Expected output: `Second platform admin created: <email>`. If the
   email is already taken (e.g. it matches the first admin), the commit
   fails with a unique-constraint error and nothing is created — pick a
   different email and re-run.
4. **User:** hand the temporary password to the second admin through a
   secure channel (not chat history), and tell them to sign in at
   `/platform/login`.
5. **Second admin (browser):** sign in with the temporary password. They
   are forced to change it immediately: `must_change_password=True` is
   enforced server-side (`app/core/deps.py` — a flagged user can only
   reach `/change-password`, `/me`, `/logout` until the password is
   actually changed), and the platform change-password endpoint is
   `POST /api/platform/auth/change-password`.
6. **Second admin (verify):** after the password change, sign in again
   and confirm the Platform portal loads the tenant list. That proves the
   account works and the forced-change flag cleared.
7. **User:** record the second admin's existence (name + email, **not**
   the password) in the break-glass note (M6's other half: where the
   server, PAT, Neon, and domain live). The password belongs to the
   second admin alone.

## Things that do not work (do not try these)

- Re-running `python -m app.seed` — skips, by design (see above).
- Creating the account as a tenant admin and "promoting" it — the role
  guard blocks `SUPER_ADMIN` on both user create and user edit, and a
  tenant-scoped user can never become tenant-less through the API.
- Signing in as the first admin and using the Platform portal — there is
  no such screen or endpoint.

## Related notes

- There is **no "last platform admin" guard** to worry about: no endpoint
  deletes or disables platform accounts at all, so creating a second one
  cannot lock anyone out and deleting one is only possible via the same
  server shell.
- The first admin's own password change (pending since 2026-11-06) is a
  separate step — the in-app flow for it is `POST
  /api/platform/auth/change-password` while signed in as that admin.
- The tenant-delete / platform-login audit gap (M7) is closed by
  migration 0024 (2026-11-12): `audit_logs.tenant_id` is now nullable,
  so platform actions are recorded in the audit table with `tenant_id`
  NULL. A second admin's actions are therefore in `audit_logs` — but
  they do NOT appear in any tenant's audit-log view, because that
  endpoint filters by the caller's tenant_id. To see platform actions,
  query the database directly (`SELECT * FROM audit_logs WHERE
  tenant_id IS NULL ORDER BY created_at DESC`).
