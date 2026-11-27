#!/usr/bin/env bash
# One-time setup (Ubuntu/Debian or macOS with Python 3.10+). From the repo root:
#   bash deploy/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v apt-get >/dev/null; then sudo apt-get update -y && sudo apt-get install -y python3-venv; fi
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if command -v apt-get >/dev/null; then .venv/bin/python -m playwright install --with-deps chromium
else .venv/bin/python -m playwright install chromium; fi
umask 077  # everything below holds personal data or secrets: private to you
[ -f .env ] || { cp .env.example .env; echo "created .env — edit it"; }
[ -d profile ] || { cp -r profile.example profile; echo "created profile/ — fill in your details"; }
mkdir -p secrets data runs .browser-profile
# also tightens an existing install (files created before umask was set)
chmod 600 .env
chmod -R go-rwx secrets data runs .browser-profile profile
echo "next: edit profile/*, put your résumé PDF in profile/, set ANTHROPIC_API_KEY in .env (or log in to Claude Code)"
