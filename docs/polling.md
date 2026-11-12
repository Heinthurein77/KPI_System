# Polling Cadences — Intended Staleness per Surface

**Status:** documented 2026-11-12 (review finding m4). Verified against the
code at HEAD `a2acb06`; the numbers below are what the code actually does,
not aspirations.

## The policy

| Surface | Cadence | Where in code |
|---|---|---|
| KPI dashboards (admin review board, dept-admin board, employee self-assessment) | Every **5 minutes**, background silent refresh | `frontend/src/pages/DashboardPage.jsx:19` (`BOARD_POLL_INTERVAL_MS = 5 * 60 * 1000`); `frontend/src/components/dashboard/EmployeeSelfAssessment.jsx:17` (`DASHBOARD_POLL_INTERVAL_MS = 5 * 60 * 1000`) |
| Notification bell | Every **60 seconds** | `frontend/src/components/layout/NotificationBell.jsx:12` (`POLL_INTERVAL_MS = 60000`) |
| Attendance `/today` (WiFi chip, Today date, check-in button state) | Every **60 seconds**, plus on window focus, plus a full reload at local midnight | `frontend/src/pages/AttendancePage.jsx:233-248` (60 s interval, `focus` listener, midnight `setTimeout` that re-arms itself) |

## Rules that apply to all three

- **Silent refresh:** background ticks keep the old data on screen and retry
  on the next tick if the request fails — a failed tick never blanks the
  page or shows an error toast. Only the initial load shows an error state.
- **Paused when the tab is hidden:** the dashboard and Attendance ticks
  check `document.hidden` and skip while the tab is in the background, so
  an idle tab does not wake a suspended Neon compute every 5 minutes.
- **Same period only:** the dashboard tick re-checks the currently viewed
  period; it never switches period or disturbs an admin mid-edit (cards
  are keyed by submission id).

## What does NOT poll

Everything else loads once and goes stale until the user navigates or
refreshes: Templates, Users, Departments, Reports, Analytics, Audit Log,
and the Attendance monthly report/summary. That is deliberate — those
pages are not live boards. (The Attendance page used to load once and go
stale too; the 60 s `/today` refresh was added in the 2026-11-10 fix pass
precisely because the WiFi chip and check-in button froze on an open page.)

## Changing a cadence

Change the constant in the file listed above and update this doc in the
same commit. Do not add a new poller without adding a row here — the point
of this page is that the cadences live in exactly one place.
