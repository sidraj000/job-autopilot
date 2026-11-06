"""Settings from the environment / a .env file at the repo root.

Secrets (passwords, tokens) are read here and handed only to the code that
types them into a page; they are never logged, written to run artifacts,
or passed to the answer model.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "job_scout"))
import paths  # noqa: E402
ENV_FILE = Path(os.environ.get("RUNNER_ENV_FILE", ROOT / ".env"))


def _load_env_file(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


_load_env_file(ENV_FILE)


def get(name, default=None):
    return os.environ.get(name, default)


def workday_password(host):
    """Password for one Workday tenant. A per-company key wins (set by you for
    accounts that already exist with a different password), else ATS_PASSWORD.
      host "acme.wd5.myworkdayjobs.com" -> WORKDAY_PW_ACME, then ATS_PASSWORD
    One password per account: the runner only ever tries this one, so it can't
    lock the account out."""
    label = host.split(".")[0].upper().replace("-", "_")
    return get(f"WORKDAY_PW_{label}") or get("ATS_PASSWORD")


RESUME = paths.resume_pdf()
RUNS_DIR = Path(get("RUNNER_RUNS_DIR", ROOT / "runs"))
PROFILE_DIR = Path(get("RUNNER_BROWSER_PROFILE", ROOT / ".browser-profile"))
HEADLESS = get("RUNNER_HEADLESS", "1") != "0"
# Answer model: the Anthropic API when ANTHROPIC_API_KEY is set, else the
# `claude` CLI (Claude Code, uses its own login), run with every tool disabled.
CLAUDE_BIN = get("RUNNER_CLAUDE_BIN", "claude")
CLAUDE_MODEL = get("RUNNER_CLAUDE_MODEL", "claude-opus-5-5")
# Eligibility gate (checked on the live posting before any form is touched):
# target role (rubric.md), in-country, experience cap, sponsorship if you need
# it, pay above MIN_PAY.
MIN_PAY = int(get("RUNNER_MIN_PAY", "120000"))
# Many postings state no pay at all: 1 = still apply, 0 = skip them.
ALLOW_UNKNOWN_PAY = get("RUNNER_ALLOW_UNKNOWN_PAY", "1") != "0"
# Optional: approve the top-N unflagged queue jobs at the start of each run
# (same as `auto_apply.py approve --top N`). 0 = only jobs you approved.
AUTO_APPROVE_TOP = int(get("RUNNER_AUTO_APPROVE_TOP", "0"))
