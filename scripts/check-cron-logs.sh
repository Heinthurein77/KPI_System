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
# N8 (2026-11-13) — two blind spots fixed, plus a third found while fixing:
# 1. Recency window: only FAILURE lines in the last FAILURE_WINDOW_HOURS
#    (24h) — and only failures after the most recent SUCCESS — fail the
#    watch. A historical failure followed by a success is recovery, not an
#    alarm; a failure older than the window is stale news. Older failures
#    are still counted and named in the output, never silently dropped.
# 2. Hung runs: a START with no SUCCESS/FAILURE after it is a run that
#    never finished. Older than HUNG_AFTER_HOURS (2h) it is STALE (exit 1);
#    younger than that it is RUNNING (exit 0 — the job may legitimately
#    still be going when the watch runs, e.g. the 08:00 reminders at 08:30).
# 3. Stale schedule: each job has a max age since its last run (daily jobs
#    36h, monthly prune 35 days). No run at all in that long means the cron
#    itself stopped — exit 1. A log whose last success was weeks ago used
#    to report OK.
#
# Exit codes: 0 = every job healthy (or still running); 1 = a recent
# FAILURE, a STALE run/schedule, a missing log, or a log with no runs.
set -uo pipefail

LOG_DIR="${1:-${KPI_APP_DIR:-$HOME/kpi-app}}"

FAILURE_WINDOW_HOURS=24
HUNG_AFTER_HOURS=2

cutoff=$(date -d "$FAILURE_WINDOW_HOURS hours ago" '+%F %T')
hung_cutoff=$(date -d "$HUNG_AFTER_HOURS hours ago" '+%F %T')

# job-name -> log file -> max hours since the last run. All three required
# cron jobs (N1/N2, 2026-11-12) — the DB backup log lives under backups/
# because the backup script redirects itself there (N11, 2026-11-13;
# see scripts/backup-db.sh). Prune runs monthly, so its max age is 35 days.
JOBS="deadline-reminders:$LOG_DIR/deadline-reminders.log:36
prune-audit:$LOG_DIR/prune-audit.log:840
db-backup:$LOG_DIR/backups/backup.log:36"

overall=0

while IFS=: read -r job log_file max_age_hours; do
    [ -n "$job" ] || continue
    if [ ! -f "$log_file" ]; then
        echo "$job: MISSING — no log file at $log_file (job may never have run; check crontab -l)"
        overall=1
        continue
    fi
    starts=$(grep -c "job starting" "$log_file" || true)
    if [ "$starts" -eq 0 ]; then
        echo "$job: EMPTY — log exists but records no runs yet"
        overall=1
        continue
    fi
    # Timestamps are "YYYY-MM-DD HH:MM:SS", so plain string comparison
    # orders them correctly.
    last_success_ts=$(grep "job succeeded" "$log_file" | tail -n 1 | cut -d' ' -f1-2 || true)
    last_ok="${last_success_ts:-0000-00-00 00:00:00}"
    recent_failed=$(awk -v c="$cutoff" -v ok="$last_ok" \
        '(($1 " " $2) >= c) && (($1 " " $2) > ok) && /job FAILED/ { n++ }
         END { print n+0 }' "$log_file")
    total_failed=$(grep -c "job FAILED" "$log_file" || true)
    older_failed=$((total_failed - recent_failed))
    read -r w_starts w_succeeded < <(awk -v c="$cutoff" \
        '(($1 " " $2) >= c) && /job starting/ { s++ }
         (($1 " " $2) >= c) && /job succeeded/ { ok++ }
         END { print s+0, ok+0 }' "$log_file")
    last_run=$(tail -n 1 "$log_file" | cut -d' ' -f1-2)
    max_age_cutoff=$(date -d "$max_age_hours hours ago" '+%F %T')

    # Hung check: is there any outcome after the last START?
    last_start_line=$(grep -n "job starting" "$log_file" | tail -n 1 | cut -d: -f1)
    last_start_ts=$(sed -n "${last_start_line}p" "$log_file" | cut -d' ' -f1-2)
    if tail -n +"$((last_start_line + 1))" "$log_file" | grep -q "job succeeded\|job FAILED"; then
        unfinished=0
    else
        unfinished=1
    fi

    older_note=""
    if [ "$older_failed" -gt 0 ]; then
        older_note="; ignoring $older_failed older failure(s)"
    fi

    if [ "$recent_failed" -gt 0 ]; then
        last_failure=$(awk -v c="$cutoff" -v ok="$last_ok" \
            '(($1 " " $2) >= c) && (($1 " " $2) > ok) && /job FAILED/ { line=$0 }
             END { print line }' "$log_file")
        echo "$job: FAILURE — $recent_failed recent failed run(s) (last run $last_run). Last failure: $last_failure$older_note"
        overall=1
    elif [ "$unfinished" -eq 1 ] && [[ "$last_start_ts" < "$hung_cutoff" ]]; then
        echo "$job: STALE — last run started $last_start_ts with no SUCCESS/FAILURE after ${HUNG_AFTER_HOURS}h (possibly killed mid-run)$older_note"
        overall=1
    elif [[ "$last_run" < "$max_age_cutoff" ]]; then
        echo "$job: STALE — no run in the last ${max_age_hours}h (last run $last_run)$older_note"
        overall=1
    elif [ "$unfinished" -eq 1 ]; then
        echo "$job: RUNNING — last run started $last_start_ts, no outcome yet (within ${HUNG_AFTER_HOURS}h grace)$older_note"
    else
        echo "$job: OK — $w_starts run(s) in the last ${FAILURE_WINDOW_HOURS}h, $w_succeeded succeeded, 0 failed (last run $last_run)$older_note"
    fi
done <<EOF
$JOBS
EOF

exit "$overall"
