#!/usr/bin/env bash
# One unattended pass: discover new jobs, then apply to the approved queue.
# Safe to run from cron and from run_loop.sh at once (they share a lock).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p runs
exec >>runs/run.log 2>&1
exec 9>runs/apply.lock
flock -n 9 || { echo "=== $(date -Is) another run holds the lock; skipping"; exit 0; }
echo "=== $(date -Is) start"
.venv/bin/python -m job_scout.runner.discover || echo "discover failed; applying to existing queue"
.venv/bin/python -m job_scout.runner.run
.venv/bin/python job_scout/auto_apply.py status | head -1
echo "=== $(date -Is) done"
