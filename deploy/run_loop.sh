#!/usr/bin/env bash
# Continuous mode: one pass every RUNNER_LOOP_HOURS (default 2). Passes reuse
# the saved Workday session and never type a password (RUNNER_NO_PASSWORD=1);
# schedule deploy/run_once.sh from cron for a daily pass that signs in.
# Start: nohup setsid deploy/run_loop.sh >/dev/null 2>&1 &
# Stop:  kill -- -"$(cat runs/loop.pid)"
cd "$(dirname "$0")/.."
mkdir -p runs
echo $$ > runs/loop.pid
export RUNNER_NO_PASSWORD=1
while true; do
  deploy/run_once.sh || echo "=== $(date -Is) run_once exited $?" >> runs/run.log
  sleep $(( ${RUNNER_LOOP_HOURS:-2} * 3600 ))
done
