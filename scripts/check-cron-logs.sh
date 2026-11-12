#!/usr/bin/env bash
# Daily cron-log watch (M4, 2026-11-12 PM review).
#
# The cron jobs already emit distinct timestamped START / SUCCESS / FAILURE
# lines into their logs (M20 fix, 2026-11-10; backup-db.sh got the same
# contract with N1, 2026-11-12). Nobody reads those logs unless someone logs in and looks — this script is the glance: one summary line per
# job, exit code 1 if anything failed (or a log is missing, which means the
# job has never run / the cron was never installed).
#
# Usage (on the server):
#   ./scripts/check-cron-logs.sh            # logs under ~/kpi-app
#   KPI_APP_DIR=/path/to/kpi-app ./scripts/check-cron-logs.sh
#   ./scripts/check-cron-logs.sh /path/to/kpi-app
#
# Suggested daily cron (after the 08:00 reminders run), on the server:
#   30 8 * * * cd ~/kpi-app && ./scripts/check-cron-logs.sh || echo "KPI cron failure — check the logs" | mail -s "KPI cron check failed" you@example.com
# (Replace the mail line with whatever alert channel you actually use; the
# script's own output names the failing job and its last FAILURE line.)
#
# Exit codes: 0 = every job's log shows only successes; 1 = a FAILURE line
# was found, a log file is missing, or a log has no runs recorded at all.
set -uo pipefail

LOG_DIR="${1:-${KPI_APP_DIR:-$HOME/kpi-app}}"

# job-name -> log file, in the order they are reported. All three required
# cron jobs (N1/N2, 2026-11-12) — the DB backup log lives under backups/
# because the backup cron redirects there (see scripts/backup-db.sh).
JOBS="deadline-reminders:$LOG_DIR/deadline-reminders.log
prune-audit:$LOG_DIR/prune-audit.log
db-backup:$LOG_DIR/backups/backup.log"

overall=0

while IFS=: read -r job log_file; do
    [ -n "$job" ] || continue
    if [ ! -f "$log_file" ]; then
        echo "$job: MISSING — no log file at $log_file (job may never have run; check crontab -l)"
        overall=1
        continue
    fi
    starts=$(grep -c "job starting" "$log_file" || true)
    succeeded=$(grep -c "job succeeded" "$log_file" || true)
    failed=$(grep -c "job FAILED" "$log_file" || true)
    last_run=$(grep "job starting" "$log_file" | tail -n 1 | cut -d' ' -f1-2 || true)
    if [ "$starts" -eq 0 ]; then
        echo "$job: EMPTY — log exists but records no runs yet"
        overall=1
        continue
    fi
    if [ "$failed" -gt 0 ]; then
        last_failure=$(grep "job FAILED" "$log_file" | tail -n 1)
        echo "$job: FAILURE — $failed failed run(s) out of $starts ($succeeded succeeded; last run $last_run). Last failure: $last_failure"
        overall=1
    else
        echo "$job: OK — $starts run(s), $succeeded succeeded, 0 failed (last run $last_run)"
    fi
done <<EOF
$JOBS
EOF

exit "$overall"
