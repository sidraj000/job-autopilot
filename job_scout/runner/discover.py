"""Job discovery: fetch new listings, screen each live posting, queue the good ones.

  python -m job_scout.runner.discover            # fetch, screen, queue (+approve)
  python -m job_scout.runner.discover --dry-run  # screen and print, change nothing

1. job_scout/fetch_jobs.py pulls new listings (company boards, GitHub
   new-grad trackers, Jobright), already filtered by profile/search.json
   regexes and the seen store.
2. Each new posting is opened in the browser and screened by
   answerer.eligibility + run.gate: target role, location, experience cap,
   sponsorship (if you need it), pay > RUNNER_MIN_PAY — plus a 0-100 fit
   score from profile/rubric.md.
3. Passing jobs (score >= RUNNER_MIN_SCORE) go into data/to_apply.json and, with
   RUNNER_AUTO_APPROVE_DISCOVERED=1, straight into approvals.json so the
   runner applies to them. Everything screened is marked in seen.json.
"""
import argparse
import datetime as dt
import json
import subprocess
import sys

from playwright.sync_api import sync_playwright

from . import answerer, config, form
from .run import NEEDS_SPONSOR, gate

sys.path.insert(0, str(config.ROOT / "job_scout"))
import auto_apply  # noqa: E402
import seen_store  # noqa: E402
from apply_tracking import norm_url  # noqa: E402
from match_key import key as match_key  # noqa: E402

MAX_SCREEN = int(config.get("RUNNER_DISCOVER_MAX", "60"))
MIN_SCORE = int(config.get("RUNNER_MIN_SCORE", "40"))
AUTO_APPROVE = config.get("RUNNER_AUTO_APPROVE_DISCOVERED", "0") == "1"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=MAX_SCREEN, help="postings to screen this run")
    args = p.parse_args()

    answerer.assert_sandboxed()
    subprocess.run([sys.executable, str(config.ROOT / "job_scout" / "fetch_jobs.py")], check=True)
    fresh = json.loads((config.paths.DATA_DIR / "raw" / "latest.json").read_text())["jobs"]

    queue = auto_apply.load(auto_apply.TO_APPLY, [])
    approvals = auto_apply.load(auto_apply.APPROVALS, {})
    apps = auto_apply.load(auto_apply.APPLICATIONS, {})
    known = {norm_url(j["url"]) for j in queue if j.get("url")}
    applied = set(apps)

    todo, screened, added = [], [], []
    for j in fresh:
        if not (j.get("url") or "").startswith("http"):
            screened.append(j)  # nothing to apply to
        elif norm_url(j["url"]) in known or match_key(j["company"], j["title"]) in applied:
            screened.append(j)
        elif NEEDS_SPONSOR and "no_sponsorship" in (j.get("red_flags") or []):
            screened.append(j)
            print(f"skip  {j['company']} — {j['title']}: tracker marks no sponsorship")
        else:
            todo.append(j)
    # new-grad trackers first: company boards are mostly senior roles
    todo.sort(key=lambda j: j.get("source") not in ("tracker", "html_table"))
    print(f"{len(fresh)} new listing(s); screening {min(len(todo), args.max)} of {len(todo)}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        for j in todo[: args.max]:
            try:
                page.goto(j["url"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                txt = form.page_text(page, 30000)
                e = answerer.eligibility(txt, j["company"], j["title"])
                if e.get("page_error") and not form.looks_like_outage(txt):
                    e["page_error"] = False
            except Exception as ex:  # noqa: BLE001 — leave it unseen, retry tomorrow
                print(f"error {j['company']} — {j['title']}: {type(ex).__name__}")
                continue
            why = gate(e)
            if why == "PAGE_ERROR":  # outage: leave unseen, retry tomorrow
                print(f"retry {j['company']} — {j['title']}: page error")
                continue
            screened.append(j)
            if why == "CLOSED":
                why = "posting closed"
            elif why is None and e["fit_score"] < MIN_SCORE:
                why = f"fit score {e['fit_score']} < {MIN_SCORE}"
            if why:
                print(f"skip  {j['company']} — {j['title']}: {why[:160]}")
                continue
            entry = {
                "company": j["company"], "title": j["title"],
                "location": e["location"] or j.get("location", ""),
                "comp": f"up to ${e['pay_max_annual_usd']:,.0f}" if e["pay_max_annual_usd"] else "",
                "score": e["fit_score"], "h1b_sponsorship": j.get("h1b_sponsorship", "unknown"),
                "red_flags": sorted(set((j.get("red_flags") or []) + e["red_flags"])),
                "source": j.get("source", ""), "url": j["url"],
                "first_seen": dt.date.today().isoformat(),
                "posting_details": {"checked": dt.date.today().isoformat(), "evidence": e["evidence"]},
            }
            added.append(entry)
            print(f"ADD   {e['fit_score']:>3} {j['company']} — {j['title']} ({entry['location']}, {entry['comp'] or 'pay n/a'})")
        browser.close()

    if args.dry_run:
        print(f"[dry-run] would add {len(added)}, mark {len(screened)} seen")
        return
    queue = sorted(queue + added, key=lambda x: -x.get("score", 0))
    auto_apply.save(auto_apply.TO_APPLY, queue)
    if AUTO_APPROVE:
        for a in added:
            approvals[norm_url(a["url"])] = {"decision": "approve", "date": auto_apply.now(),
                                             "url": a["url"], "company": a["company"],
                                             "title": a["title"], "by": "discover (auto)"}
        auto_apply.save(auto_apply.APPROVALS, approvals)
    seen_store.mark(screened)
    print(f"added {len(added)} job(s){' (approved)' if AUTO_APPROVE else ''}; marked {len(screened)} seen")


if __name__ == "__main__":
    main()
