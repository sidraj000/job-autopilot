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
[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "created .env — edit it"; }
[ -d profile ] || { cp -r profile.example profile; echo "created profile/ — fill in your details"; }
mkdir -p secrets data runs && chmod 700 secrets
echo "next: edit profile/*, put your résumé PDF in profile/, set ANTHROPIC_API_KEY in .env (or log in to Claude Code)"
