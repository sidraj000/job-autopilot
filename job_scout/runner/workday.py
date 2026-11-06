"""Workday applications: sign in (or create the account) with ATS_EMAIL /
ATS_PASSWORD from .env, confirm a new account through Gmail (read-only), then
walk the multi-step flow — upload résumé, answer each step, Next — and submit
once at Review.

The password is only ever passed to page.fill() on Workday's own password
inputs; it is not logged, screenshotted (fields are masked), or sent to the
answer model. A visible CAPTCHA, a verification step we can't complete by
email, or a sign-in failure on an existing account -> blocked.
"""
import json
import re
import time
from urllib.parse import urlsplit

from . import answerer, config, form, gmail

NO_CREDS = "Workday sign-in needed and ATS_EMAIL/ATS_PASSWORD are not set in .env"
NO_LOGIN = "no live Workday session (RUNNER_NO_PASSWORD on) — do one normal sign-in pass to refresh it"
ACCOUNTS = config.RUNS_DIR / "workday_accounts.json"  # host -> {"created": date}; no secrets
A = lambda aid: f'[data-automation-id="{aid}"]'  # noqa: E731


def _visible(page, sel, timeout=0):
    try:
        if timeout:
            page.locator(sel).first.wait_for(state="visible", timeout=timeout)
        return page.locator(sel).first.is_visible()
    except Exception:
        return False


def _click(page, aid):
    """Workday puts a transparent click_filter div over its buttons."""
    btn = page.locator(A(aid)).first
    overlay = btn.locator("xpath=preceding-sibling::*[@data-automation-id='click_filter'][1]")
    (overlay if overlay.count() else btn).click(timeout=10000)


def _errors(page):
    msgs = page.locator(f'{A("errorMessage")}, {A("errorBanner")}, {A("inputAlert")}').all_inner_texts()
    return [m.strip() for m in msgs if m.strip()]


def _step(page):
    try:
        return page.locator(A("progressBarActiveStep")).first.inner_text().split("\n")[-1].strip()
    except Exception:
        return ""


def _accounts():
    return json.loads(ACCOUNTS.read_text()) if ACCOUNTS.exists() else {}


def _remember(host):
    acc = _accounts()
    acc.setdefault(host, {"created": time.strftime("%Y-%m-%d")})
    ACCOUNTS.write_text(json.dumps(acc, indent=1))


AUTH_SEL = ", ".join(A(x) for x in ("SignInWithEmailButton", "email", "signInSubmitButton",
                                     "createAccountSubmitButton"))
READY_SEL = ", ".join([AUTH_SEL] + [A(x) for x in ("autofillWithResume", "applyManually",
                                                   "progressBar", "pageFooterNextButton")])


def _wait_ready(page, timeout=25000):
    try:
        page.locator(READY_SEL).first.wait_for(state="visible", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(1500)


def _had_account(host):
    """Past email from this tenant's Workday (e.g. acme@myworkday.com) means
    an account already exists for ATS_EMAIL."""
    try:
        return gmail.count(f"in:anywhere from:{host.split('.')[0]}@myworkday.com") > 0
    except Exception:  # no token / API error: fall back to trying account creation
        return False


def _signed_in(page):
    """In the application flow with no auth form showing."""
    _wait_ready(page)
    if _visible(page, AUTH_SEL):
        return False
    step = _step(page)
    return bool(step) and not re.search(r"sign in|create account", step, re.I)


def _sign_in(page, email, pw):
    if _visible(page, A("signInLink")) and not _visible(page, A("signInSubmitButton")):
        _click(page, "signInLink")
        page.wait_for_timeout(1500)
    page.locator(A("email")).fill(email)
    page.locator(A("password")).fill(pw)
    _click(page, "signInSubmitButton")
    page.wait_for_timeout(6000)
    return _signed_in(page)


def _create(page, email, pw):
    if not _visible(page, A("createAccountSubmitButton")):
        _click(page, "createAccountLink")
        page.wait_for_timeout(1500)
    page.locator(A("email")).fill(email)
    page.locator(A("password")).fill(pw)
    page.locator(A("verifyPassword")).fill(pw)
    box = page.locator(A("createAccountCheckbox"))
    if box.count() and not box.is_checked():
        box.check(force=True)  # the account T&C consent (standing_answers.agree_to_consents in profile.json)
    _click(page, "createAccountSubmitButton")
    page.wait_for_timeout(7000)


HISTORY_SECTIONS = ("Work-Experience-section", "Education-section")


def _panels(page, section):
    sec = page.locator(f'[aria-labelledby="{section}"]')
    return sec, sec.locator("[role=group][aria-labelledby$='-panel']")


def _resize(page, section, n):
    """Delete extra panels / add missing ones until the section has exactly n."""
    sec, panels = _panels(page, section)
    if not sec.count():
        return False
    for _ in range(15):
        have = panels.count()
        if have == n:
            return True
        if have > n:
            delete = panels.nth(have - 1).get_by_role("button", name="Delete", exact=True)
            if not delete.count():  # some tenants pin the first panel: that's the floor
                return n == 0
            delete.click(timeout=4000)
        else:
            _close_popups(page)
            sec.locator(A("add-button")).last.click(timeout=4000)
        page.wait_for_timeout(1200)
    return panels.count() == n


def _by_suffix(panel, suffix):
    return panel.locator(f'[id$="--{suffix}"]').first


def _type_date(page, el, digits):
    """Workday month/year sections auto-advance: type MMYYYY (or YYYY) into the
    first section in one go, as a person would."""
    el.evaluate("e => e.focus()")
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.keyboard.type(digits, delay=110)
    el.evaluate("e => e.blur()")
    page.wait_for_timeout(300)


def _set_text(panel, suffix, value):
    el = _by_suffix(panel, suffix)
    if el.count() and value is not None:
        el.fill(str(value))
        return True
    return False


SEEN = []  # option lists seen on misses, dumped with the history problems


def _visible_options(page):
    try:
        return [t.strip() for t in page.locator(
            "[data-automation-id='promptOption']:visible, [role=listbox] [role=option]:visible").all_inner_texts()][:40]
    except Exception:  # noqa: BLE001
        return []


def _close_popups(page):
    """Multiselect prompts stay open after a pick and cover the next panel's
    buttons (seen as click timeouts on several tenants): Escape and click neutral page space."""
    for _ in range(2):
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    try:
        page.locator("h2, h3").first.click(timeout=1500, position={"x": 2, "y": 2})
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(300)


def _pick(page, button, rx):
    """Workday listbox button: open, choose the first option matching rx.
    rx may be a list: tried in order (most specific first), so "master" can
    never land on "Master of Education" when "Master of Science" exists."""
    _close_popups(page)
    button.click(timeout=4000)
    page.wait_for_timeout(700)
    for one in ([rx] if isinstance(rx, str) else rx):
        opt = page.locator("[role=listbox] [role=option]:visible").filter(has_text=re.compile(one, re.I))
        if opt.count():
            opt.first.click(timeout=4000)
            page.wait_for_timeout(400)
            return True
    SEEN.append({"listbox": _visible_options(page)})
    page.keyboard.press("Escape")
    return False


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _set_school(page, panel, e):
    """School is plain text on some tenants and a search prompt on others
    (some tenants). Prompt: exact (punctuation-insensitive) match against
    the profile's school name or its schoolOptions — never a nearest guess."""
    el = _by_suffix(panel, "schoolName")
    if not el.count():
        el = _by_suffix(panel, "school")  # prompt tenants: education-N--school
    if not el.count():
        return False
    box = el.locator("xpath=ancestor::*[starts-with(@data-automation-id,'formField')][1]")
    is_prompt = box.locator("[data-automation-id='multiselectInputContainer'], [data-automation-id='promptIcon']").count() > 0
    if not is_prompt:
        el.fill(e["school"])
        page.keyboard.press("Escape")  # close any suggestion list so it can't cover Degree
        el.evaluate("e => e.blur()")
        page.wait_for_timeout(300)
        return True
    if box.locator("[data-automation-id='selectedItem']").count():
        cur = _norm(box.locator("[data-automation-id='selectedItem']").first.inner_text())
        if cur in {_norm(n) for n in [e["school"], *e.get("schoolOptions", [])]}:
            return True
        for x in box.locator("[data-automation-id='DELETE_charm']").all():
            x.click()
    for name in [e["school"], *e.get("schoolOptions", [])]:
        el.click()
        el.fill(name)
        page.keyboard.press("Enter")
        want = _norm(name)
        hits = page.locator("[data-automation-id='promptOption']:visible, [role=option]:visible")
        for _ in range(8):  # search results can take a few seconds
            page.wait_for_timeout(700)
            if hits.count():
                break
        page.wait_for_timeout(500)
        for i in range(hits.count()):
            # an option can carry extra lines (location, a checkbox label): any line matching counts
            if want in {_norm(line) for line in hits.nth(i).inner_text().splitlines()} | {_norm(hits.nth(i).inner_text())}:
                hits.nth(i).click()
                page.wait_for_timeout(600)
                return True
        SEEN.append({"school_search": name, "options": _visible_options(page)})
        page.keyboard.press("Escape")
        el.fill("")
    return False


def _fill_job(page, panel, j):
    bad = []
    for suffix, key in (("jobTitle", "title"), ("companyName", "company"), ("location", "location"),
                        ("roleDescription", "description")):
        if not _set_text(panel, suffix, j.get(key)) and suffix in ("jobTitle", "companyName"):
            bad.append(suffix)
    cur = _by_suffix(panel, "currentlyWorkHere")
    if cur.count() and cur.is_checked() != bool(j.get("current")):
        cur.evaluate("e => (e.labels && e.labels[0] || e).click()")
        page.wait_for_timeout(500)
    for part, key in (("startDate", "start"), ("endDate", "end")):
        el = _by_suffix(panel, f"{part}-dateSectionMonth-input")
        if part == "endDate" and j.get("current"):
            continue
        if el.count() and j.get(key):
            mm, yyyy = j[key].split("/")
            yr = _by_suffix(panel, f"{part}-dateSectionYear-input")
            for _ in range(2):
                _type_date(page, el, f"{int(mm):02d}{yyyy}")
                if el.input_value().lstrip("0") == mm.lstrip("0") and yr.input_value() == yyyy:
                    break
            else:
                bad.append(f"{part} (got {el.input_value()}/{yr.input_value()}, want {j[key]})")
        elif el.count():
            bad.append(part)
    return bad


def _fill_school(page, panel, e):
    bad = []
    if not _set_school(page, panel, e):
        bad.append(f"schoolName (no option named {e['school']!r}; add schoolOptions in profile.json)")
    deg = _by_suffix(panel, "degree")
    if deg.count() and not _pick(page, deg, e.get("degreeRx") or re.escape(e["degree"])):
        bad.append("degree (no acceptable option; see degreeRx in profile.json)")
    fos = _by_suffix(panel, "fieldOfStudy")
    if fos.count() and e.get("field"):
        box = fos.locator("xpath=ancestor::*[starts-with(@data-automation-id,'formField')][1]")
        # tenants name majors differently: try the profile's field, then its listed
        # alternatives (fieldOptions), exact match only — never a nearest guess
        for name in [e["field"], *e.get("fieldOptions", [])]:
            if box.locator("[data-automation-id='selectedItem']").count():
                break
            fos.click()
            fos.fill(name)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            hit = page.locator("[data-automation-id='promptOption']:visible, [role=option]:visible").filter(
                has_text=re.compile(rf"^\s*{re.escape(name)}\s*$", re.I))
            if hit.count():
                hit.first.click()
                page.wait_for_timeout(600)
            else:
                SEEN.append({"field_search": name, "options": _visible_options(page)})
                page.keyboard.press("Escape")
                fos.fill("")
        _close_popups(page)
        if not box.locator("[data-automation-id='selectedItem']").count():
            bad.append(f"fieldOfStudy (no option named {e['field']!r}; add fieldOptions in profile.json)")
    _set_text(panel, "gradeAverage", e.get("gpa"))
    for suffix, key in (("firstYearAttended", "start"), ("lastYearAttended", "end")):
        el = _by_suffix(panel, f"{suffix}-dateSectionYear-input")
        if el.count() and e.get(key):
            want = e[key].split("/")[-1]
            _type_date(page, el, want)
            if el.input_value() != want:
                _type_date(page, el, want)
            if el.input_value() != want:
                bad.append(f"{suffix} (got {el.input_value()!r}, want {want})")
    return bad


def fill_history(page, dump):
    """My Experience: make Work Experience / Education match profile.json's
    employment / educationEntries exactly — delete whatever the résumé parser injected (it turns
    projects into fake jobs), add the real entries, fill each panel by its field
    ids. Never goes to the model: per-field model answers can't tell panels apart
    (they once wrote one employer into all six job panels). Returns problems."""
    from .answers_table import EDUCATION, EMPLOYMENT
    problems = []
    SEEN.clear()
    for section, entries, filler in (("Work-Experience-section", EMPLOYMENT, _fill_job),
                                     ("Education-section", EDUCATION, _fill_school)):
        if not page.locator(f'[aria-labelledby="{section}"]').count():
            continue
        # start from empty panels: clearing résumé-parsed values in place is unreliable
        # (a parsed month "4" survived and became 04/2026)
        _close_popups(page)
        try:
            sized = _resize(page, section, 0) and _resize(page, section, len(entries))
        except Exception as ex:  # noqa: BLE001
            sized = False
            SEEN.append({"error": str(ex)[:400]})
        if not sized:
            problems.append(f"{section}: could not set {len(entries)} panels")
            continue
        _, panels = _panels(page, section)
        for i, entry in enumerate(entries):
            try:
                bad = filler(page, panels.nth(i), entry)
            except Exception as ex:  # noqa: BLE001
                bad = [type(ex).__name__]
                SEEN.append({"error": str(ex)[:400]})
            if bad:
                problems.append(f"{section} #{i + 1}: {', '.join(bad)}")
                try:  # field ids + labels of the failing panel, to fix selectors from the dump
                    SEEN.append({"panel": f"{section} #{i + 1}", "fields": panels.nth(i).evaluate(
                        """p => [...p.querySelectorAll('input,button,textarea,[data-automation-id]')].slice(0, 60)
                            .map(e => [e.tagName, e.id, e.getAttribute('data-automation-id'),
                                       (e.getAttribute('aria-label') || e.innerText || '').slice(0, 60)].join(' | '))""")})
                except Exception:  # noqa: BLE001
                    pass
    dump("history", {"employment": len(EMPLOYMENT), "education": len(EDUCATION), "problems": problems,
                    "seen_options": SEEN})
    return problems


def in_history_sections(page, fields):
    """Ids of extracted fields that live inside the history sections (handled by
    fill_history, so they must not be re-answered by the table/model)."""
    ids = [f["id"] for f in fields]
    return set(page.evaluate(
        """([ids, secs]) => ids.filter(id => { const e = document.querySelector(`[data-jr-id="${id}"]`);
             return e && secs.some(s => e.closest(`[aria-labelledby="${s}"]`)); })""",
        [ids, list(HISTORY_SECTIONS)]))


def _reset_password(page, host, email, pw, back_to_form, shot):
    """Forgot-password -> Gmail reset link -> set password to ATS_PASSWORD ->
    sign in. Returns None on success, else a reason string. Opt-in (the caller
    checks RUNNER_AUTO_RESET_PASSWORD); this actually changes the account password."""
    tenant = host.split(".")[0]
    if not _visible(page, A("forgotPasswordLink")):
        back_to_form()
        if _visible(page, A("SignInWithEmailButton")):
            _click(page, "SignInWithEmailButton")
            page.wait_for_timeout(1500)
    if not _visible(page, A("forgotPasswordLink")):
        return f"auto-reset: no 'Forgot password' link on {tenant}"
    since = time.time()
    _click(page, "forgotPasswordLink")
    page.wait_for_timeout(2000)
    if _visible(page, A("email")):
        page.locator(A("email")).fill(email)
    for aid in ("resetPasswordSubmitButton", "submitButton", "forgotPasswordSubmitButton"):
        if _visible(page, A(aid)):
            _click(page, aid)
            break
    else:
        page.keyboard.press("Enter")
    page.wait_for_timeout(4000)
    if form.captcha_visible(page):
        return "captcha on Workday password-reset request"
    try:
        msg = gmail.wait_for_message(
            f"in:anywhere from:{tenant}@myworkday.com (reset OR password)", since, timeout=300)
    except FileNotFoundError as e:
        return f"auto-reset needs Gmail token: {e}"
    link = None
    for raw in re.findall(r'https?://[^\s"\'<>]+', msg or ""):
        u = raw.replace("&amp;", "&")
        if host in u and re.search(r"reset|password|token|forgot", u, re.I):
            link = u
            break
    if not link:
        return "auto-reset: no reset link in email within 5 min"
    page.goto(link, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    shot("reset-1-page")
    if not _visible(page, A("password")):
        return "auto-reset: reset page has no password field (expired link?)"
    page.locator(A("password")).fill(pw)
    if _visible(page, A("verifyPassword")):
        page.locator(A("verifyPassword")).fill(pw)
    for aid in ("submitButton", "changePasswordSubmitButton", "resetPasswordSubmitButton", "signInSubmitButton"):
        if _visible(page, A(aid)):
            _click(page, aid)
            break
    page.wait_for_timeout(5000)
    errs = " ".join(_errors(page))
    if errs:
        shot("reset-2-error")
        return f"auto-reset failed setting new password: {errs[:150]}"
    # back to the job and sign in with the new password
    back_to_form()
    if _visible(page, A("SignInWithEmailButton")):
        _click(page, "SignInWithEmailButton")
        page.wait_for_timeout(1500)
    if _signed_in(page) or _sign_in(page, email, pw):
        print(f"  auto-reset: {tenant} password reset to ATS_PASSWORD and signed in")
        return None
    shot("reset-3-signin")
    return f"auto-reset: password changed but sign-in still failed ({' '.join(_errors(page))[:120]})"


def authenticate(page, host, back_to_form, shot=lambda tag: None):
    """Returns None when signed in, else a reason string (-> blocked).
    shot(tag) screenshots each stage (password inputs render masked)."""
    email, pw = config.get("ATS_EMAIL"), config.workday_password(host)
    tenant = host.split(".")[0]
    per_tenant = bool(config.get(f"WORKDAY_PW_{tenant.upper().replace('-', '_')}"))
    if _signed_in(page):
        return None
    # No-password mode (used when iterating on the flow): never type the
    # password or create an account — only a session restored from an earlier
    # real sign-in counts. If that session is gone, stop and ask for one pass.
    if config.get("RUNNER_NO_PASSWORD") == "1":
        return NO_LOGIN
    if not (email and pw):
        return NO_CREDS
    if _visible(page, A("SignInWithEmailButton")):
        _click(page, "SignInWithEmailButton")
        page.wait_for_timeout(2000)
    if form.captcha_visible(page):
        return "captcha on Workday sign-in"
    if _sign_in(page, email, pw):
        _remember(host)
        return None
    shot("auth-1-signin")
    err = " ".join(_errors(page))[:200]
    if host in _accounts():
        return f"Workday sign-in failed on an account the runner set up: {err}"
    # Sign-in failed on an existing account (we have past emails or a per-tenant
    # password that didn't work). With auto-reset enabled, reset the password to
    # ATS_PASSWORD via "Forgot password" + the Gmail reset link; else report.
    existing = per_tenant or _had_account(host)
    if existing and config.get("RUNNER_AUTO_RESET_PASSWORD") == "1" :
        rr = _reset_password(page, host, email, pw, back_to_form, shot)
        if rr is None:
            _remember(host)
            return None
        return rr
    if per_tenant:
        return (f"sign-in failed for existing {tenant} account with WORKDAY_PW_{tenant.upper()} "
                f"— wrong password or account locked; fix it and unblock. ({err[:80]})")
    if existing:
        return (f"existing {tenant} Workday account (past emails found) — set WORKDAY_PW_"
                f"{tenant.upper()} in .env, reset it to ATS_PASSWORD, or enable "
                f"RUNNER_AUTO_RESET_PASSWORD. ({err[:80]})")

    since = time.time()
    _create(page, email, pw)
    shot("auth-2-created")
    if form.captcha_visible(page):
        return "captcha on Workday account creation"
    errs = " ".join(_errors(page))
    if re.search(r"already|exists|in use", errs, re.I):
        return ("a Workday account already exists for this email with a different password "
                "(sign in once by hand, or reset it to ATS_PASSWORD): " + errs[:150])
    if errs:
        return f"Workday account creation failed: {errs[:200]}"
    if _signed_in(page):
        _remember(host)
        return None

    # Some tenants just need a sign-in after creating; others email a link first.
    back_to_form()
    if _visible(page, A("SignInWithEmailButton")):
        _click(page, "SignInWithEmailButton")
        page.wait_for_timeout(2000)
    if _visible(page, A("email")) and _sign_in(page, email, pw):
        _remember(host)
        return None
    shot("auth-3-signin-after-create")
    try:
        msg = gmail.wait_for_message("in:anywhere from:(myworkday.com OR workday.com OR myworkdayjobs.com)", since)
    except FileNotFoundError as e:
        return f"account may need email verification: {e}"
    link = gmail.find_link(msg, host) if msg else None
    if not link:
        return ("Workday account not usable yet: sign-in after creating it failed "
                f"({' '.join(_errors(page))[:150]}) and no verification email arrived within 5 min")
    page.goto(link, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    shot("auth-4-verified")
    back_to_form()
    if _visible(page, A("SignInWithEmailButton")):
        _click(page, "SignInWithEmailButton")
        page.wait_for_timeout(2000)
    if not _signed_in(page) and not _sign_in(page, email, pw):
        shot("auth-5-signin-after-verify")
        return f"sign-in after verification failed: {' '.join(_errors(page))[:200]}"
    _remember(host)
    return None


def open_apply(page, url):
    """Job page -> Apply -> 'Autofill with Resume' (falls back to manual)."""
    page.goto(url.split("?")[0], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    if _visible(page, A("legalNoticeDeclineButton")):
        page.locator(A("legalNoticeDeclineButton")).click()  # decline non-essential cookies
    if _visible(page, A("invalid-content")):
        return "closed"
    # "Apply" on a fresh posting, "Continue Application" when a draft exists
    # (already signed in). Either resumes the flow.
    start = "adventureButton" if _visible(page, A("adventureButton"), timeout=8000) else (
        "continueButton" if _visible(page, A("continueButton")) else None)
    if not start:
        return "closed"
    page.locator(A(start)).first.click()
    _wait_ready(page)
    for choice in ("autofillWithResume", "applyManually"):
        if _visible(page, A(choice)):
            page.locator(A(choice)).first.click()
            _wait_ready(page)
            break
    return None


def apply(page, job, dry, record, shot, inflight, dump=lambda tag, data: None):
    host = urlsplit(job["url"]).netloc
    if open_apply(page, job["url"]) == "closed":
        return record(job["url"], "blocked", "posting closed", dry)
    why = authenticate(page, host, lambda: open_apply(page, job["url"]), shot)
    if why in (NO_CREDS, NO_LOGIN):  # not the job's fault: leave it approved
        print(f"  left pending: {why}")
        return "needs-creds"
    if why:
        shot("auth")
        return record(job["url"], "blocked", why, dry)

    posting = job.get("posting_text", "")
    seen_steps, uploaded = [], False
    for _ in range(12):
        page.wait_for_timeout(3000)
        if _visible(page, "text=Something went wrong"):
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            if _visible(page, "text=Something went wrong"):
                shot("wd-error")
                return record(job["url"], "failed", f"Workday error page at step {_step(page)!r}", dry)
        if form.captcha_visible(page):
            return record(job["url"], "blocked", "captcha in Workday flow", dry)
        # résumé-source choice screen (appears first, and again when resuming a
        # draft): take the manual form so we control every field.
        if _visible(page, A("applyManually")) or _visible(page, A("autofillWithResume")):
            choice = "applyManually" if _visible(page, A("applyManually")) else "autofillWithResume"
            page.locator(A(choice)).first.click()
            _wait_ready(page)
            continue

        step = _step(page)
        seen_steps.append(step)
        if seen_steps.count(step) > 2:
            shot("stuck")
            return record(job["url"], "failed", f"stuck on step {step!r}: {' | '.join(_errors(page))[:300]}", dry)

        if re.search(r"review", step, re.I):
            break
        if not uploaded:
            attached = page.locator(A("file-upload-successful")).count()
            if attached == 0 and page.locator(A("file-upload-input-ref")).count():
                page.locator(A("file-upload-input-ref")).first.set_input_files(str(config.RESUME))
                page.wait_for_timeout(8000)  # Workday parses the résumé into later steps
                uploaded = True
            elif attached > 0:  # resumed draft: keep one résumé, delete duplicates
                while page.locator(A("delete-file")).count() > 1:
                    page.locator(A("delete-file")).last.click()
                    page.wait_for_timeout(1500)
                uploaded = True

        history = bool(re.search(r"experience", step, re.I))
        if history:
            problems = fill_history(page, dump)
            if problems:
                shot(f"history-{len(seen_steps)}")
                return record(job["url"], "blocked", f"work/education entries ({step}): " + "; ".join(problems), dry)

        fields = [f for f in form.read_fields(page) if f["kind"] != "file"]
        if history:
            skip = in_history_sections(page, fields)
            fields = [f for f in fields if f["id"] not in skip]
        bot_q = [f["label"] for f in fields if form.BOT_CHECK_RX.search(f["label"])]
        if bot_q:
            return record(job["url"], "blocked", f"bot/human check, apply by hand: {bot_q[0][:150]}", dry)
        if fields:
            answers, needs = answerer.answer(fields, posting, job["company"], job["title"])
            dump(f"step{len(seen_steps)}-{re.sub(r'[^a-z0-9]+', '-', step.lower())}",
                 {"step": step, "fields": fields, "answers": answers, "needs": needs})
            if needs:
                shot(f"needs-{len(seen_steps)}")
                qs = "; ".join(n["question"][:120] for n in needs[:5])
                return record(job["url"], "blocked", f"needs you ({step}): {qs}", dry)
            unfilled = form.fill_all(page, fields, answers)
            if unfilled:
                shot(f"unfilled-{len(seen_steps)}")
                return record(job["url"], "failed", f"could not fill ({step}): " + "; ".join(unfilled), dry)
        if not _visible(page, A("pageFooterNextButton"), timeout=8000):
            shot(f"no-next-{len(seen_steps)}")
            return record(job["url"], "failed", f"no Next/Save button on step {step!r}", dry)
        _click(page, "pageFooterNextButton")
    else:
        return record(job["url"], "failed", f"never reached Review (steps: {seen_steps})", dry)

    before = shot("review")
    if dry:
        print(f"  [dry-run] reached Review after {len(seen_steps)} step(s); screenshot {before}")
        return "dry-run"
    inflight(True)
    _click(page, "pageFooterNextButton")  # "Submit" on the Review step
    page.wait_for_timeout(8000)
    if form.confirmed(page) or re.search(r"application submitted|congratulations", form.page_text(page), re.I):
        after = shot("confirmation")
        inflight(False)
        return record(job["url"], "submitted", f"Workday confirmation seen; screenshot {after}", dry)
    after = shot("after-submit")
    inflight(False)
    errs = _errors(page)
    if errs and _visible(page, A("pageFooterNextButton")):
        return record(job["url"], "failed", "Workday rejected submit: " + " | ".join(errs)[:400], dry)
    return record(job["url"], "unverified", f"no confirmation seen; screenshot {after}", dry)
