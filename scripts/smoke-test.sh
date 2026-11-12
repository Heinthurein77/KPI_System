#!/usr/bin/env bash
# Pre-deploy smoke test (M2, 2026-11-12 PM review).
#
# Boots the stack against a SCRATCH database and hits /readyz + /login, so a
# bad build or migration is caught before it reaches production. Run on the
# server after `git pull`, before the production `docker compose up -d`:
#
#   ./scripts/smoke-test.sh
#
# What it does:
#   1. Builds the image from the current checkout and starts the local stack
#      (docker-compose.local.yml: web + a throwaway Postgres) as a separate
#      compose project ("kpi-smoke"), so it never touches the live containers,
#      the live volumes, or the Neon database.
#   2. Polls /readyz until it answers {"status": "ok"} (SELECT 1 against the
#      scratch DB — proves migrations ran and the app can serve traffic).
#   3. GETs /login and requires HTTP 200 (proves the frontend bundle built
#      and is being served by the SPA fallback).
#   4. Tears everything down (`down -v`), including the scratch database.
#
# Exit code is non-zero on any failure. Nothing here runs on this VM's
# local checkout: Docker is not installed here, so the compose path can
# only be exercised on the server. What WAS verified here: `bash -n` on
# this script, plus the same two endpoints against a backend booted
# directly on a scratch SQLite DB (see the fix-pass report).
#
# Env overrides:
#   SMOKE_PROJECT      compose project name (default: kpi-smoke)
#   SMOKE_COMPOSE_FILE compose file (default: docker-compose.local.yml)
#   SMOKE_BASE_URL     base URL of the smoke stack (default: http://127.0.0.1:8000)
#   SMOKE_TIMEOUT      seconds to wait for /readyz (default: 180)
set -euo pipefail

cd "$(dirname "$0")/.."

PROJECT="${SMOKE_PROJECT:-kpi-smoke}"
COMPOSE_FILE="${SMOKE_COMPOSE_FILE:-docker-compose.local.yml}"
BASE_URL="${SMOKE_BASE_URL:-http://127.0.0.1:8000}"
TIMEOUT="${SMOKE_TIMEOUT:-180}"

compose() {
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" "$@"
}

cleanup() {
    # Always remove the scratch stack and its database volume, even on failure,
    # so a failed smoke run never leaves a stray Postgres behind.
    compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Start clean: a previous interrupted run must not be mistaken for this one.
compose down -v --remove-orphans >/dev/null 2>&1 || true

echo "smoke-test: building + starting scratch stack (project $PROJECT)..."
compose up -d --build --wait

echo "smoke-test: waiting up to ${TIMEOUT}s for $BASE_URL/readyz ..."
deadline=$((SECONDS + TIMEOUT))
while true; do
    if curl -fsS --max-time 5 "$BASE_URL/readyz" 2>/dev/null | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        echo "smoke-test: /readyz OK"
        break
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "smoke-test: FAIL — /readyz did not answer OK within ${TIMEOUT}s" >&2
        compose logs web 2>/dev/null | tail -50 >&2 || true
        exit 1
    fi
    sleep 2
done

echo "smoke-test: checking $BASE_URL/login ..."
http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/login")
if [ "$http_code" != "200" ]; then
    echo "smoke-test: FAIL — /login returned HTTP $http_code, expected 200" >&2
    exit 1
fi
echo "smoke-test: /login OK (HTTP 200)"

echo "smoke-test: PASS — stack boots against a scratch DB, /readyz + /login answer."
