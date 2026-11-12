# Deletion Policy — Every Delete Is Hard and Permanent

**Status:** ACCEPTED decision, recorded 2026-11-12 (review finding m9).
There is no soft-delete, no recycle bin, and no un-delete anywhere in the
system. This page records that as a deliberate decision, not an oversight —
do not "fix" it by building soft-delete without a new user decision.

## What each delete destroys

| Action | Who can do it | What is permanently lost | Guard | Audit entry |
|---|---|---|---|---|
| Delete a **user** | Tenant admin only (`app/routers/admin.py:597`, `DELETE /users/{id}`) | The user's own KPI submission history. Submissions they only *reviewed* are kept, detached from the reviewer (`app/services/kpi_service.py:87` `force_delete_user`). | Cannot delete your own signed-in account; cannot delete the last other tenant admin (sole-admin guard — add another admin first). | Yes — `user_deleted` |
| Delete a **KPI template** (tenant admin) | Tenant admin (`app/routers/admin.py:1133`, via `force_delete_template`) | The template **and every submission recorded against it, any status**. | None — unconditional by design. | Yes — `kpi_template_deleted` ("including all submission history") |
| Delete a **KPI template** (dept admin) | Dept admin, own department only | The template and its draft submissions only. | Blocked (400) once any non-draft submission exists against it — history stays intact. Also blocked for a template assigned to the admin personally, and for other departments' templates. | Yes — `kpi_template_deleted` |
| Delete a **department** | Tenant admin (`app/routers/admin.py:114`, `DELETE /departments/{id}`) | The department row only (it has no history of its own). | Blocked (400) while any user or KPI template is still assigned to it — reassign or remove those first. | Yes — `department_deleted` (M7 fix, 2026-11-12) |
| Delete a **tenant** | Platform super-admin only (`app/routers/platform.py:162`, `DELETE /tenants/{id}`) | Everything under the tenant: departments, users, templates, submissions — all cascade via FK. The most destructive action in the system. | Suspend-first: the tenant must already be `SUSPENDED`; a live tenant cannot be deleted. Deliberately a two-step action. | Yes — `tenant_deleted`, logged platform-scoped (`tenant_id` NULL, migration 0024) so the record survives the tenant it describes |
| Delete an **attendance record** | Tenant admin / dept admin (own dept) | The record only. | Only **leave** records can be deleted; check-in/out records are rejected (400). | Yes — attendance mutations are audited |

## Mitigations (why hard-delete is acceptable)

1. **Guards before the delete** — sole-admin, dept-admin history block,
   department non-empty block, tenant suspend-first. The dangerous paths
   cannot be hit by accident or by a single click.
2. **Audit entries** — every delete writes an audit-log row (who, when,
   what, including the metric name and weight for templates). Tenant and
   department deletes were the two unaudited paths (M7) and are now
   audited too — `tenant_deleted` (platform-scoped, survives the delete)
   and `department_deleted`, both added in the 2026-11-12 fix pass.
3. **Backups** — the nightly DB dump (`scripts/backup-db.sh`) and Neon
   point-in-time recovery are the only way back after a delete. See
   `docs/backup-and-restore.md`. A delete older than the 6-hour Neon PITR
   window is recoverable only from the nightly dump.

## What this decision does NOT cover

- Login events (`login_success` / `login_failed`, tenant and platform)
  are audited too since the same 2026-11-12 fix pass (M7) — see
  `app/routers/auth.py` and `app/routers/platform.py`.
- If the user later wants a recycle bin, a confirm-with-typing step, or
  soft-delete, that is a new feature decision, not a correction of this
  page.
