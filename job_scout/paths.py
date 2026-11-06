"""Where your profile and the pipeline's state live (stdlib only).

  profile/  (JOB_PROFILE_DIR)  your facts: profile.json, cv.md, notes.md,
                               rubric.md, search.json, résumé PDF. Git-ignored.
  data/     (JOB_DATA_DIR)     queue, approvals, apply log, seen store,
                               fetch snapshots. Git-ignored.
If profile/ doesn't exist yet, profile.example/ is used so you can try the
fetcher, but the runner refuses to submit with the example profile.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = ROOT / "profile.example"
PROFILE_DIR = Path(os.environ.get("JOB_PROFILE_DIR", ROOT / "profile"))
if not PROFILE_DIR.exists():
    PROFILE_DIR = EXAMPLE_DIR
DATA_DIR = Path(os.environ.get("JOB_DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRETS_DIR = Path(os.environ.get("JOB_SECRETS_DIR", ROOT / "secrets"))


def using_example():
    return PROFILE_DIR.resolve() == EXAMPLE_DIR.resolve()


def profile():
    return json.loads((PROFILE_DIR / "profile.json").read_text())


def search_config():
    return json.loads((PROFILE_DIR / "search.json").read_text())


def resume_pdf():
    return PROFILE_DIR / profile().get("resume_pdf", "resume.pdf")


def full_name():
    i = profile()["identity"]
    return f"{i['first']} {i['last']}"
