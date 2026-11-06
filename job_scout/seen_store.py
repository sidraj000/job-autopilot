#!/usr/bin/env python3
"""Persistent record of job URLs already processed by the job scout, so
repeat runs never re-fetch or re-score the same listing twice.

Store lives at data/seen.json — {key: {company, title, score, first_seen}}.
key is the job URL, falling back to "company::title" when a source has no URL.

Usage:
  python3 job_scout/seen_store.py mark <jobs.json>
      Adds every job in <jobs.json> (a list of job dicts, or an object with a
      "jobs" list) to the seen store. Existing entries are left untouched.
"""
import json
import sys
from datetime import date
from pathlib import Path

from paths import DATA_DIR

STORE = DATA_DIR / "seen.json"


def load():
    if STORE.exists():
        return json.loads(STORE.read_text())
    return {}


def save(store):
    STORE.write_text(json.dumps(store, indent=1, sort_keys=True))


def key_for(job):
    return job.get("url") or f'{job.get("company", "")}::{job.get("title", "")}'


def is_seen(job, store=None):
    store = load() if store is None else store
    return key_for(job) in store


def mark(jobs, store=None):
    store = load() if store is None else store
    added = 0
    for j in jobs:
        k = key_for(j)
        if k not in store:
            store[k] = {
                "company": j.get("company", ""),
                "title": j.get("title", ""),
                "score": j.get("score"),
                "first_seen": date.today().isoformat(),
            }
            added += 1
    save(store)
    return added, store


def main():
    if len(sys.argv) != 3 or sys.argv[1] != "mark":
        print("usage: seen_store.py mark <jobs.json>", file=sys.stderr)
        return 1
    payload = json.loads(Path(sys.argv[2]).read_text())
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    added, store = mark(jobs)
    print(f"marked {added} new job(s) as seen (store now has {len(store)} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
