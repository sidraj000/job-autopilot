#!/usr/bin/env python3
"""Approval-gated auto-apply queue (the browser agent's bookkeeping).

Nothing is ever submitted unless that exact job was approved first (by you,
or by discovery when RUNNER_AUTO_APPROVE_DISCOVERED=1). Decisions live in
data/approvals.json and attempts in data/apply_log.json — separate from
data/to_apply.json so discovery, which rewrites the queue, can never drop an
approval.

  review [--write]                 numbered list of undecided queue jobs
                                   (--write also saves jobs/approval_list.md)
  approve 1 3 5-8 | --top N [--include-flagged] | --url URL ...
  skip    2 4 | --url URL ...      hide from future reviews, never apply
  status                           approved/pending, today's submits, blocked
  next [--cap N]                   JSON work list for the browser agent
  record URL --status S --note T   S: submitted | unverified | blocked | failed
  unblock URL                      make a blocked/unverified/failed job eligible again (resets retries)

"Numbers" are positions in the current `review` output.  Stdlib only.
"""
import argparse
import datetime as dt
import json
import re
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "job_scout"))
from apply_tracking import norm_url, record_applied  # noqa: E402
from paths import DATA_DIR, resume_pdf  # noqa: E402

TO_APPLY = DATA_DIR / "to_apply.json"
APPROVALS = DATA_DIR / "approvals.json"
APPLY_LOG = DATA_DIR / "apply_log.json"
APPLICATIONS = DATA_DIR / "applications.json"
APPROVAL_LIST = DATA_DIR / "approval_list.md"

DAILY_CAP = int(os.environ.get("JOB_DAILY_CAP", "10"))  # set JOB_DAILY_CAP high to uncap
MAX_FAILED_ATTEMPTS = 2
# Red flags that keep a job out of `approve --top N`; it can still be
# approved explicitly by number or URL.
GATING_FLAGS = {"requires_phd", "no_sponsorship", "requires_clearance", "us_citizen_only"}
# Statuses that stop the agent from touching a job again on its own.
# "unverified" = submit was clicked but no confirmation seen: retrying could
# file a duplicate application, so a human checks first.
STOP_STATUSES = {"submitted", "unverified", "blocked"}


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save(path, data):
    path.write_text(json.dumps(data, indent=1) + "\n")


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def ats(url):
    host = urlparse(url).netloc.lower()
    for needle, name in (("ashbyhq", "ashby"), ("greenhouse", "greenhouse"),
                         ("lever.co", "lever"), ("myworkdayjobs", "workday"),
                         ("workable", "workable"), ("smartrecruiters", "smartrecruiters"),
                         ("jobright", "jobright")):
        if needle in host:
            return name
    return "other"


def posting_id(url):
    """ATS-level identity of a posting, so two url forms of the same job
    (Workday /en-US/ vs bare, ?gh_jid= vs /jobs/<id>, tracker utm params)
    never get applied to twice."""
    u = urlparse(url)
    host, path = u.netloc.lower(), u.path.rstrip("/")
    m = re.search(r"gh_jid=(\d+)", u.query) or re.search(r"greenhouse\.io/.*?/jobs/(\d+)", host + path)
    if m:
        return f"gh:{m.group(1)}"
    if "myworkdayjobs" in host:
        m = re.search(r"_((?:J?R)?[-\w]*\d+(?:-\d+)?)$", path)
        if m:
            return f"wd:{host.split('.')[0]}:{m.group(1).lower()}"
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", path)
    if m:
        return f"uuid:{m.group(1)}"
    return norm_url(url)


def attempts(log, nu):
    return [a for a in log if a["key"] == nu]


def last_status(log, nu):
    a = attempts(log, nu)
    return a[-1]["status"] if a else None


def undecided(queue, approvals, log):
    """The `review` ordering — approve/skip resolve numbers against it."""
    return [j for j in queue
            if j.get("url", "").startswith("http")  # no link = nothing to apply to
            and norm_url(j["url"]) not in approvals
            and last_status(log, norm_url(j["url"])) not in STOP_STATUSES]


def describe(j):
    flags = ",".join(j.get("red_flags") or []) or "-"
    comp = (j.get("posting_details") or {}).get("comp") or j.get("comp") or ""
    return (f'{j["score"]:>3}  {j["company"]} — {j["title"]}  '
            f'[{ats(j["url"])}] flags:{flags}{"  " + comp if comp else ""}')


def parse_numbers(tokens, n):
    picked = set()
    for t in tokens:
        lo, _, hi = t.partition("-")
        rng = range(int(lo), int(hi or lo) + 1)
        for i in rng:
            if not 1 <= i <= n:
                sys.exit(f"number {i} is outside the review list (1-{n})")
            picked.add(i)
    return sorted(picked)


def cmd_review(args):
    queue, approvals, log = load(TO_APPLY, []), load(APPROVALS, {}), load(APPLY_LOG, [])
    todo = undecided(queue, approvals, log)
    lines = [f"{i:>3}. {describe(j)}\n     {j['url']}" for i, j in enumerate(todo, 1)]
    print("\n".join(lines) or "Nothing awaiting a decision.")
    if args.write:
        APPROVAL_LIST.write_text(
            f"# Approval list ({dt.date.today()})\n\n"
            "Approve with `python3 job_scout/auto_apply.py approve <numbers>` "
            "(numbers match this list only while the queue is unchanged).\n\n"
            "```\n" + "\n".join(lines) + "\n```\n")
        print(f"\nwrote {APPROVAL_LIST}")


def decide(args, decision):
    queue, approvals, log = load(TO_APPLY, []), load(APPROVALS, {}), load(APPLY_LOG, [])
    todo = undecided(queue, approvals, log)
    chosen = [todo[i - 1] for i in parse_numbers(args.numbers, len(todo))]
    if getattr(args, "top", None):
        pool = [j for j in todo if args.include_flagged
                or not GATING_FLAGS & set(j.get("red_flags") or [])]
        chosen += pool[: args.top]
    for u in args.url or []:
        j = next((j for j in queue if norm_url(j["url"]) == norm_url(u)), None)
        if not j:
            sys.exit(f"not in to_apply.json: {u}")
        chosen.append(j)
    if not chosen:
        sys.exit("nothing selected")
    seen = set()
    for j in chosen:
        nu = norm_url(j["url"])
        if nu in seen:
            continue
        seen.add(nu)
        approvals[nu] = {"decision": decision, "date": now(), "url": j["url"],
                         "company": j["company"], "title": j["title"]}
        print(f"{decision}: {describe(j)}")
    save(APPROVALS, approvals)


def cmd_status(_args):
    queue, approvals, log = load(TO_APPLY, []), load(APPROVALS, {}), load(APPLY_LOG, [])
    in_queue = {norm_url(j["url"]) for j in queue}
    today = dt.date.today().isoformat()
    pending = [a for nu, a in approvals.items() if a["decision"] == "approve"
               and nu in in_queue and last_status(log, nu) not in STOP_STATUSES]
    print(f"queue: {len(queue)} | undecided: {len(undecided(queue, approvals, log))} | "
          f"approved & pending: {len(pending)} | "
          f"submitted today: {sum(a['status'] == 'submitted' and a['date'].startswith(today) for a in log)}")
    for a in pending:
        print(f"  pending  {a['company']} — {a['title']}")
    for nu in dict.fromkeys(a["key"] for a in log):
        att = attempts(log, nu)
        if att[-1]["status"] in ("blocked", "unverified"):
            print(f"  {att[-1]['status']:<9}{att[-1]['company']} — {att[-1]['title']}: {att[-1]['note']}")


def cmd_next(args):
    queue, approvals, log = load(TO_APPLY, []), load(APPROVALS, {}), load(APPLY_LOG, [])
    today = dt.date.today().isoformat()
    done_today = sum(a["status"] == "submitted" and a["date"].startswith(today) for a in log)
    budget = max(0, (args.cap if args.cap is not None else DAILY_CAP) - done_today)
    work, dropped = [], []
    by_url = {norm_url(j["url"]): j for j in queue}
    # same posting under another url: done already, or already in this list
    latest = {}
    for x in log:
        latest[x["key"]] = x
    taken = {posting_id(x["url"]) for x in latest.values() if x["status"] in STOP_STATUSES}
    for nu, a in sorted(approvals.items(), key=lambda kv: -by_url.get(kv[0], {}).get("score", 0)):
        if a["decision"] != "approve" or last_status(log, nu) in STOP_STATUSES:
            continue
        att = attempts(log, nu)
        since = max((i for i, x in enumerate(att) if x["status"] == "unblocked"), default=-1)
        if sum(x["status"] == "failed" for x in att[since + 1:]) >= MAX_FAILED_ATTEMPTS:
            continue
        j = by_url.get(nu)
        if not j:  # nightly liveness sweep removed it (posting closed)
            dropped.append(a["url"])
            continue
        pid = posting_id(j["url"])
        if pid in taken:
            continue
        taken.add(pid)
        work.append({
            "url": j["url"], "company": j["company"], "title": j["title"],
            "ats": ats(j["url"]), "score": j["score"],
            "red_flags": j.get("red_flags") or [],
            "previous_attempts": attempts(log, nu),
        })
    print(json.dumps({"budget_today": budget, "submitted_today": done_today,
                      "resume": str(resume_pdf()), "jobs": work[:budget],
                      "approved_but_closed": dropped}, indent=1))


def cmd_record(args):
    queue, approvals, log = load(TO_APPLY, []), load(APPROVALS, {}), load(APPLY_LOG, [])
    nu = norm_url(args.url)
    j = next((j for j in queue if norm_url(j["url"]) == nu), None) or approvals.get(nu)
    if not j:
        sys.exit(f"unknown job (not queued or approved): {args.url}")
    if args.status == "submitted" and approvals.get(nu, {}).get("decision") != "approve":
        sys.exit("refusing: this job was never approved")
    if last_status(log, nu) == "submitted":
        sys.exit("already recorded as submitted")
    entry = {"key": nu, "url": j["url"], "company": j["company"], "title": j["title"],
             "status": args.status, "note": args.note, "date": now()}
    log.append(entry)
    save(APPLY_LOG, log)
    print(f"recorded {args.status}: {j['company']} — {j['title']}")
    if args.status == "submitted":
        imported, pruned = record_applied({nu: {**entry, "via": "auto-apply agent"}})
        print(f"applications.json: {imported} update(s) | to_apply.json: {pruned} pruned")


def cmd_unblock(args):
    log = load(APPLY_LOG, [])
    nu = norm_url(args.url)
    if last_status(log, nu) not in ("blocked", "unverified", "failed"):
        sys.exit("job is not blocked/unverified/failed")
    a = attempts(log, nu)[-1]
    log.append({**a, "status": "unblocked", "note": "cleared by user", "date": now()})
    save(APPLY_LOG, log)
    print(f"unblocked: {a['company']} — {a['title']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review"); r.add_argument("--write", action="store_true")
    r.set_defaults(fn=cmd_review)
    for name, decision in (("approve", "approve"), ("skip", "skip")):
        s = sub.add_parser(name)
        s.add_argument("numbers", nargs="*")
        s.add_argument("--url", action="append")
        if name == "approve":
            s.add_argument("--top", type=int)
            s.add_argument("--include-flagged", action="store_true")
        s.set_defaults(fn=lambda a, d=decision: decide(a, d))
    sub.add_parser("status").set_defaults(fn=cmd_status)
    n = sub.add_parser("next"); n.add_argument("--cap", type=int); n.set_defaults(fn=cmd_next)
    rc = sub.add_parser("record")
    rc.add_argument("url")
    rc.add_argument("--status", required=True, choices=["submitted", "unverified", "blocked", "failed"])
    rc.add_argument("--note", required=True)
    rc.set_defaults(fn=cmd_record)
    u = sub.add_parser("unblock"); u.add_argument("url"); u.set_defaults(fn=cmd_unblock)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
