"""Apply to today's approved jobs headlessly.

  python -m job_scout.runner.run              # submit (respects the 10/day cap)
  python -m job_scout.runner.run --dry-run    # fill everything, never submit
  python -m job_scout.runner.run --url URL --dry-run   # one job (must be approved)

Work list and bookkeeping go through job_scout/auto_apply.py (`next` /
`record`), the same path the Chrome agent uses. Jobs on ATSs without an
adapter are listed in jobs/needs_you.md instead of being touched.
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import time
import traceback

from playwright.sync_api import sync_playwright

from . import adapters, answerer, config, form, gmail, workday

NEEDS_SPONSOR = str(config.paths.profile()["work_auth"].get("needs_sponsorship", "No")).lower() == "yes"

AUTO_APPLY = [sys.executable, str(config.ROOT / "job_scout" / "auto_apply.py")]
INFLIGHT = config.RUNS_DIR / "inflight.json"
SESSION = config.RUNS_DIR / "session.json"
NEEDS_YOU = config.paths.DATA_DIR / "needs_you.md"


def sh(args):
    return subprocess.run(AUTO_APPLY + args, capture_output=True, text=True, check=True).stdout


def record(url, status, note, dry):
    if dry:
        print(f"  [dry-run] would record {status}: {note}")
        return status
    sh(["record", url, "--status", status, "--note", note[:900]])
    print(f"  recorded {status}: {note[:200]}")
    return status


def skip(url, reason, dry):
    """Ineligible job: flip its approval to skip so it never comes back."""
    print(f"  {'[dry-run] would skip' if dry else 'skipped'}: {reason}")
    if not dry:
        sh(["skip", "--url", url])
    return "skipped"


def ineligible_reason(job, posting_text):
    """None if the job passes the gate (role, location, sponsorship, pay)."""
    if NEEDS_SPONSOR and "no_sponsorship" in (job.get("red_flags") or []):
        return "queue flag: no_sponsorship"
    e = answerer.eligibility(posting_text, job["company"], job["title"])
    if e.get("page_error") and not form.looks_like_outage(posting_text):
        e["page_error"] = False  # healthy posting the model mis-flagged
    return gate(e)


def gate(e):
    """Your rules over an eligibility() result: None = apply,
    "CLOSED" = posting gone, else the human-readable reason to skip."""
    if e.get("page_error"):
        return "PAGE_ERROR"
    if not e.get("posting_available", True):
        return "CLOSED"
    pay = e["pay_max_annual_usd"]
    why = [r for ok, r in (
        (e["role_match"], "not a target role"),
        (e["location_ok"], "location outside your country"),
        (not e.get("exceeds_experience"), "requires more experience than your cap"),
        (not (NEEDS_SPONSOR and e["explicitly_no_sponsorship"]), "posting says no visa sponsorship"),
        (pay is None and config.ALLOW_UNKNOWN_PAY or pay is not None and pay > config.MIN_PAY,
         f"pay {'not stated' if pay is None else f'tops out at ${pay:,.0f}'} (need > ${config.MIN_PAY:,})"),
    ) if not ok]
    return f"{'; '.join(why)} — {e['evidence'][:200]}" if why else None


def recover_inflight(dry):
    """A crash after clicking submit must never lead to a second submit."""
    if not INFLIGHT.exists():
        return
    for url in json.loads(INFLIGHT.read_text()):
        try:
            record(url, "unverified", "runner stopped right after clicking submit; check email", dry)
        except subprocess.CalledProcessError:
            pass
    INFLIGHT.unlink()


CODE_MAIL_QUERY = ('(subject:"security code" OR subject:"verification" OR subject:"verify" '
                   'OR subject:"validation code") -from:jobright.ai')


def _enter_emailed_code(page, since):
    """None on success, else the reason it couldn't be done."""
    try:
        text = gmail.wait_for_message(CODE_MAIL_QUERY, since, timeout=240, poll=15)
    except Exception as e:  # noqa: BLE001 — token missing / Gmail down
        return f"Gmail unavailable ({type(e).__name__})"
    if not text:
        return "no code email within 4 min"
    code = gmail.find_security_code(text)
    if not code:
        return "code email found but no code parsed"
    if not form.enter_security_code(page, code):
        return "no code field / verify button on the page"
    return None


def apply_one(page, job, run_dir, dry):
    ad = adapters.for_url(job["url"])
    def shot(tag):
        path = run_dir / f"{job['slug']}-{tag}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    if NEEDS_SPONSOR and "no_sponsorship" in (job.get("red_flags") or []):
        return skip(job["url"], "queue flag: no_sponsorship", dry)
    page.goto(job["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    text = form.page_text(page, 30000)
    if form.looks_like_outage(text):  # often just a slow load: give it one more chance
        page.wait_for_timeout(8000)
        text = form.page_text(page, 30000)
    reason = ineligible_reason(job, text)
    if reason == "CLOSED":
        return record(job["url"], "blocked", "posting closed", dry)
    if reason == "PAGE_ERROR":  # site outage: retry on a later run, record nothing
        print("  page error / outage; will retry next run")
        return "retry"
    if reason:
        return skip(job["url"], reason, dry)

    if ad is adapters.WORKDAY:
        job["posting_text"] = form.page_text(page, 20000)

        def inflight(on):
            INFLIGHT.write_text(json.dumps([job["url"]])) if on else INFLIGHT.unlink(missing_ok=True)

        def dump(tag, data):  # per-step fields/answers for debugging
            (run_dir / f"{job['slug']}-{tag}.json").write_text(json.dumps(data, indent=1))
        return workday.apply(page, job, dry, record, shot, inflight, dump)

    ad.open_form(page, job["url"])
    if form.captcha_visible(page):
        return record(job["url"], "blocked", "captcha on load", dry)

    fields = form.read_fields(page)
    has_form = any(f["kind"] == "email" or "email" in f["label"].lower() for f in fields)
    if not has_form:
        return record(job["url"], "blocked", f"no application form found (posting closed?) at {page.url}", dry)

    bot_q = [f["label"] for f in fields if form.BOT_CHECK_RX.search(f["label"])]
    if bot_q:  # anti-automation question: never answered by the runner
        return record(job["url"], "blocked", f"form asks a bot/human check, apply by hand: {bot_q[0][:150]}", dry)

    resume = form.resume_inputs(fields)
    if not resume:
        return record(job["url"], "blocked", "no résumé upload field found", dry)
    form.loc(page, resume[0]["id"]).set_input_files(str(config.RESUME))
    page.wait_for_timeout(4000)  # ATS résumé parsers prefill fields; re-read after
    covers = form.cover_letter_inputs(page, fields, resume[0]["id"])
    if covers:
        letter = answerer.cover_letter(form.page_text(page), job["company"], job["title"])
        if not letter:
            return record(job["url"], "blocked", "needs you: required cover letter (model draft rejected)", dry)
        pdf = run_dir / f"{job['slug']}-cover-letter.pdf"
        (run_dir / f"{job['slug']}-cover-letter.txt").write_text(letter)
        form.write_pdf(page.context, letter, pdf)
        form.loc(page, covers[0]["id"]).set_input_files(str(pdf))
        page.wait_for_timeout(2500)
    fields = [f for f in form.read_fields(page) if f["kind"] != "file"]

    answers, needs = answerer.answer(fields, form.page_text(page), job["company"], job["title"])
    (run_dir / f"{job['slug']}-answers.json").write_text(json.dumps(
        {"fields": fields, "answers": answers, "needs": needs}, indent=1))
    if needs:
        qs = "; ".join(n["question"][:120] for n in needs[:5])
        return record(job["url"], "blocked", f"needs you: {qs}", dry)

    unfilled = form.fill_all(page, fields, answers)
    if unfilled:
        shot("prefill")
        return record(job["url"], "failed", "could not fill: " + "; ".join(unfilled), dry)

    before = shot("before-submit")
    if dry:
        print(f"  [dry-run] filled {len(answers)} field(s); screenshot {before}")
        return "dry-run"
    if form.captcha_visible(page):
        return record(job["url"], "blocked", "captcha before submit", dry)

    INFLIGHT.write_text(json.dumps([job["url"]]))
    submitted_at = time.time()
    ad.submit_button(page).click()
    page.wait_for_timeout(6000)
    if form.confirmed(page):
        after = shot("confirmation")
        INFLIGHT.unlink()
        return record(job["url"], "submitted", f"confirmation seen; screenshot {after}", dry)
    if form.captcha_visible(page):
        INFLIGHT.unlink()
        shot("captcha")
        return record(job["url"], "blocked", "captcha after submit (not solved; finish by hand)", dry)
    if form.SECURITY_CODE_RX.search(form.page_text(page, 60000)):
        # emailed one-time code: read it from your inbox (Gmail App
        # Password or OAuth token, optional) and enter it. CAPTCHAs are never solved.
        shot("security-code")
        why = _enter_emailed_code(page, submitted_at)
        if why is None:
            page.wait_for_timeout(6000)
            if form.confirmed(page):
                after = shot("confirmation")
                INFLIGHT.unlink(missing_ok=True)
                return record(job["url"], "submitted", f"emailed code entered; confirmation seen; screenshot {after}", dry)
            if form.captcha_visible(page):
                INFLIGHT.unlink(missing_ok=True)
                shot("captcha")
                return record(job["url"], "blocked", "captcha after security code (not solved; finish by hand)", dry)
            why = "code entered but no confirmation seen"
        after = shot("after-code")
        INFLIGHT.unlink(missing_ok=True)
        status = "unverified" if why.startswith("code entered") else "blocked"
        return record(job["url"], status, f"emailed security code: {why}; screenshot {after}", dry)
    errs = form.validation_errors(page)
    after = shot("after-submit")
    INFLIGHT.unlink()
    still_on_form = ad.submit_button(page).is_visible()
    if errs and still_on_form:  # the ATS rejected the form client-side: nothing was filed
        return record(job["url"], "failed", "validation errors: " + " | ".join(errs), dry)
    return record(job["url"], "unverified", f"no confirmation seen; screenshot {after}", dry)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="fill forms but never click submit or record")
    p.add_argument("--url", help="only this approved job")
    p.add_argument("--cap", type=int, help="override today's remaining budget (lower only)")
    args = p.parse_args()

    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if config.paths.using_example() and not args.dry_run:
        sys.exit("profile/ not found: copy profile.example/ to profile/ and fill in your details "
                 "(the example profile can only be used with --dry-run)")
    if not config.RESUME.exists():
        sys.exit(f"résumé not found: {config.RESUME} (set resume_pdf in profile.json)")
    answerer.assert_sandboxed()
    recover_inflight(args.dry_run)
    if config.AUTO_APPROVE_TOP and not args.dry_run:
        subprocess.run(AUTO_APPLY + ["approve", "--top", str(config.AUTO_APPROVE_TOP)], check=False)

    budget = json.loads(sh(["next"]))["budget_today"]
    if args.cap is not None:
        budget = min(budget, args.cap)
    # Pull every approved job: ineligible ones are skipped without using budget.
    jobs = json.loads(sh(["next", "--cap", "100000"]))["jobs"]
    if args.url:
        jobs = [j for j in jobs if j["url"].split("?")[0].rstrip("/") == args.url.split("?")[0].rstrip("/")]
        if not jobs:
            sys.exit("that URL is not in today's approved work list (approve it first)")
    supported = [j for j in jobs if adapters.for_url(j["url"])]
    manual = [j for j in jobs if not adapters.for_url(j["url"])]

    run_dir = config.RUNS_DIR / dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True)
    print(f"{len(supported)} job(s) to run, {len(manual)} need you; artifacts in {run_dir}")

    no_pw = config.get("RUNNER_NO_PASSWORD") == "1"
    with sync_playwright() as pw:
        if no_pw and SESSION.exists():  # reuse a saved sign-in; never type the password
            browser = pw.chromium.launch(headless=config.HEADLESS)
            ctx = browser.new_context(storage_state=str(SESSION),
                                      viewport={"width": 1400, "height": 1000})
        else:
            ctx = pw.chromium.launch_persistent_context(str(config.PROFILE_DIR), headless=config.HEADLESS,
                                                        viewport={"width": 1400, "height": 1000})
        submitted = 0
        for job in supported:
            if submitted >= budget and not args.dry_run:
                print(f"daily budget reached ({budget})")
                break
            job["slug"] = "".join(c if c.isalnum() else "-" for c in f"{job['company']}-{job['title']}")[:80]
            job["form_url"] = adapters.for_url(job["url"]).form_url(job["url"])
            print(f"- {job['company']} — {job['title']}")
            page = ctx.new_page()
            try:
                if apply_one(page, job, run_dir, args.dry_run) == "submitted":
                    submitted += 1
            except Exception as e:  # noqa: BLE001
                (run_dir / f"{job['slug']}-error.txt").write_text(traceback.format_exc())
                if INFLIGHT.exists():
                    INFLIGHT.unlink()
                    record(job["url"], "unverified", f"error after submit click: {type(e).__name__}", args.dry_run)
                else:
                    record(job["url"], "failed", f"runner error: {type(e).__name__}: {str(e)[:300]}", args.dry_run)
            finally:
                page.close()
        try:  # persist the authenticated session so a no-password pass can reuse it
            config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(SESSION))
        except Exception:
            pass
        ctx.close()

    if manual:
        lines = [f"- [ ] **{j['company']}** — {j['title']} [{j['ats']}]\n      {j['url']}" for j in manual]
        NEEDS_YOU.write_text(f"# Needs you ({dt.date.today()})\n\nApproved jobs on ATSs the runner "
                             "can't do yet. Apply by hand, then "
                             "`python3 job_scout/auto_apply.py record URL --status submitted --note ...`.\n\n"
                             + "\n".join(lines) + "\n")
        print(f"wrote {NEEDS_YOU} ({len(manual)} job(s))")


if __name__ == "__main__":
    main()
