"""Generic form reading/filling on a Playwright page (ATS-agnostic)."""
import re
from pathlib import Path

from . import config

EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text()
RESUME_RX = re.compile(r"resume|résumé|\bcv\b", re.I)
CONFIRM_RX = re.compile(
    r"thank(s| you)( so much)? for (applying|your (application|interest))|application (has been |was )?"
    r"(submitted|received)|successfully (applied|submitted)|we('ve| have) received your application",
    re.I)
# Questions about the applicant being automated/human (not about LLM skills).
BOT_CHECK_RX = re.compile(
    r"automated (script|tool|bot|submission|application|software)|spoof|captcha|"
    r"are you (a |an )?(human|robot|bot|ai agent|ai|llm|language model)\b|"
    r"\b(ai agent|llm|bot|robot)\b[^?]{0,20}\bor (a |an )?human|human or (a |an )?(ai|bot|robot|llm)|"
    r"prove (that )?you('re| are) (a )?human|select the (first|second|third|fourth|fifth) option", re.I)
# Post-submit "we emailed you a code to confirm you're a human" (Greenhouse):
# the runner reads the code from Gmail (read-only token) when one is configured.
SECURITY_CODE_RX = re.compile(r"verification code was sent|enter the \d+-character code|"
                              r"security code.{0,80}confirm you.?re a human|confirm you.?re a human|emailed you a code to verify your identity|enter your validation code", re.I | re.S)
# Categories that hold job-source leaves like "LinkedIn" in nested Workday prompts
WD_SOURCE_CATEGORIES = ["Job Boards?", "Social Media", "Online( Job Boards?)?", "Internet", "Websites?", "Job Sites?"]
CAPTCHA_SELECTORS = [
    "iframe[src*='recaptcha/api2/bframe']", "iframe[src*='recaptcha/enterprise/bframe']",
    "iframe[src*='hcaptcha.com'][title*='challenge' i]", "iframe[src*='challenges.cloudflare.com']",
    "#px-captcha", "[class*='captcha-challenge' i]",
]


def loc(page, fid):
    return page.locator(f'[data-jr-id="{fid}"]').first


def read_fields(page):
    fields = page.evaluate(EXTRACT_JS)
    for f in fields:
        f["label"] = re.sub(r"\s*✱.*$", "", f["label"]).strip()
        if f["kind"] == "wd_select":
            f["options"] = wd_select_options(page, f["id"])
        if f["kind"] == "combobox":
            f["options"] = combobox_options(page, f["id"])
            if len(f["options"]) >= 100:  # paginated list (schools, cities): search it instead
                f["options"] = []
    return fields


def _options_of(page, el):
    """The option elements belonging to this combobox's own (open) listbox."""
    lb = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
    if lb:
        return page.locator(f'[id="{lb}"] [role=option]')
    return page.locator("[role=listbox]:visible [role=option]")


def _close(page, el):
    page.keyboard.press("Escape")
    try:
        el.evaluate("e => e.blur()")
        page.locator("[role=listbox]").first.wait_for(state="hidden", timeout=1500)
    except Exception:
        pass


def combobox_options(page, fid, limit=200):
    """Open a react-select style combobox and read its listed options.
    An empty list means type-to-search (e.g. city): any typed value is allowed."""
    el = loc(page, fid)
    try:
        el.click(timeout=3000)
        page.wait_for_timeout(500)
        opts = _options_of(page, el).all_inner_texts()[:limit]
    except Exception:
        opts = []
    finally:
        _close(page, el)
    opts = [o.strip() for o in opts if o.strip()]
    if len(opts) == 1 and re.search(r"no options|type to search|start typing", opts[0], re.I):
        return []
    return opts


def wd_select_options(page, fid):
    """Workday dropdown button: open, read [role=option], close."""
    el = loc(page, fid)
    try:
        el.click(timeout=4000)
        page.wait_for_timeout(600)
        opts = page.locator("[role=listbox]:visible [role=option]").all_inner_texts()
    except Exception:
        opts = []
    finally:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    return [o.strip() for o in opts if o.strip() and o.strip().lower() != "select one"]


COVER_RX = re.compile(r"cover\s*letter", re.I)


def cover_letter_inputs(page, fields, resume_id):
    """Required cover-letter file inputs (optional ones are left empty). The
    label can be just "Attach" (Greenhouse), so also read the input's id/name
    and the nearest ancestor text that names the group ("Cover Letter*")."""
    out = []
    for f in fields:
        if f["kind"] != "file" or f["id"] == resume_id:
            continue
        info = loc(page, f["id"]).evaluate("""e => {
            // first ancestor whose first line is a real group label, not the "Attach" button text
            let t = '', n = e.parentElement;
            for (let i = 0; i < 8 && n; i++, n = n.parentElement) {
                const first = ((n.innerText || '').trim().split('\\n')[0] || '').trim();
                if (first && !/^(attach|upload|browse|choose file|drop files here)$/i.test(first)) { t = first; break; }
            }
            return {id: e.id || '', name: e.name || '', req: e.required || e.getAttribute('aria-required') === 'true',
                    text: t.slice(0, 120)};
        }""")
        names = f"{f['label']} {info['id']} {info['name']} {info['text']}"
        if COVER_RX.search(names.replace("_", " ")) and (
                f.get("required") or info["req"] or re.search(r"cover\s*letter\s*\*", names, re.I)):
            out.append(f)
    return out


def write_pdf(context, text, path):
    """Render plain text to a one-page-ish PDF with the browser itself."""
    import html
    p = context.new_page()
    body = "".join(f"<p>{html.escape(par).replace(chr(10), '<br>')}</p>" for par in text.split("\n\n"))
    p.set_content("<html><body style='font: 11pt Georgia, serif; line-height: 1.45; margin: 0'>"
                  f"<h3 style='margin:0 0 14px'>{html.escape(config.paths.full_name())}</h3>{body}</body></html>")
    p.pdf(path=str(path), format="Letter", margin={"top": "0.9in", "bottom": "0.9in", "left": "1in", "right": "1in"})
    p.close()


def resume_inputs(fields):
    files = [f for f in fields if f["kind"] == "file" and not f.get("autofill")]
    named = [f for f in files if RESUME_RX.search(f["label"])]
    return named[:1] or files[:1]


def as_bool(value):
    """Coerce a checkbox answer to a real bool (string 'No'/'false' -> False,
    not bool('No')==True)."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "y", "1", "on", "checked")


def fill(page, f, value):
    el = loc(page, f["id"])
    k = f["kind"]
    if k in ("text", "email", "tel", "url", "number", "date", "textarea"):
        if (el.get_attribute("data-automation-id") or "").startswith("dateSection"):
            # Workday date sections are zero-width spinbuttons that auto-advance,
            # so .click() can't hit them and a neighbour can steal the digits.
            # Focus the exact section via the DOM, clear it, type, retry once.
            try:
                el.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            for _ in range(2):
                el.evaluate("e => e.focus()")
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                page.keyboard.type(str(value), delay=100)
                el.evaluate("e => e.blur()")
                page.wait_for_timeout(300)
                if (el.input_value() or "").strip():
                    break
        else:
            el.fill(str(value))
            page.wait_for_timeout(900)
            # typeahead text inputs (e.g. Lever "Current location") need a suggestion picked
            sugg = page.locator("[role=option]:visible, .pac-item:visible, "
                                "[class*='dropdown-location'] li:visible, [class*='suggestion']:visible")
            hit = sugg.filter(has_text=re.compile(re.escape(str(value).split(",")[0]), re.I))
            if hit.count():
                hit.first.click()
            else:
                el.press("Tab")  # Workday/React commit values on blur
    elif k == "wd_select":
        el.click()
        page.wait_for_timeout(600)
        sel = "[role=listbox] [role=option]:visible, [data-automation-id='promptOption']:visible"
        opts = page.locator(sel)
        # long searchable dropdowns (Country, Phone Type, …): type to filter first
        if opts.count() == 0 or opts.count() > 8:
            page.keyboard.type(re.split(r"\s*\(", str(value))[0][:25], delay=25)
            page.wait_for_timeout(1000)
            opts = page.locator(sel)
        target = opts.filter(has_text=re.compile(rf"^\s*{re.escape(str(value))}\s*$"))
        if not target.count():  # prefix match (e.g. "United States of America" vs "...(+1)")
            target = opts.filter(has_text=re.compile(re.escape(re.split(r"\s*\(", str(value))[0]), re.I))
        if not target.count():
            page.keyboard.press("Escape")
            raise LookupError(f"no wd_select option for {value!r}")
        target.first.click(timeout=6000)
        page.wait_for_timeout(500)
    elif k == "wd_multiselect":
        el.click()
        el.fill(str(value))
        el.press("Enter")
        page.wait_for_timeout(1500)
        opts = page.locator("[data-automation-id='promptOption']:visible")
        hit = opts.filter(has_text=re.compile(re.escape(str(value)), re.I))
        if not hit.count():
            # nested prompt (e.g. "How did you hear": Job Board > LinkedIn):
            # search only covers the top level, so walk the likely categories
            el.fill("")
            page.wait_for_timeout(400)
            leaf = re.compile(rf"^\s*{re.escape(str(value))}\s*$", re.I)
            for cat in WD_SOURCE_CATEGORIES:
                el.click()
                page.wait_for_timeout(1000)
                c = page.locator("[data-automation-id='promptOption']:visible").filter(
                    has_text=re.compile(rf"^\s*{cat}\s*$", re.I))
                if not c.count():
                    page.keyboard.press("Escape")
                    continue
                c.first.click()
                page.wait_for_timeout(1200)
                hit = page.locator("[data-automation-id='promptOption']:visible").filter(has_text=leaf)
                if hit.count():
                    break
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
        if not hit.count():
            page.keyboard.press("Escape")
            raise LookupError(f"no option matching {value!r}")
        hit.first.click()
        page.wait_for_timeout(800)
        page.keyboard.press("Escape")
    elif k == "select":
        el.select_option(label=value)
    elif k == "checkbox":
        el.set_checked(as_bool(value))
    elif k in ("radio", "checkbox_group"):
        wanted = value if isinstance(value, list) else [value]
        for v in wanted:
            opt = loc(page, f["optionIds"][f["options"].index(v)])
            try:
                opt.check(timeout=3000)
            except Exception:  # styled inputs: click the visible label instead
                opt.evaluate("e => (e.labels && e.labels[0] || e).click()")
    elif k == "yesno":
        el.get_by_role("button", name=value, exact=True).click()
    elif k == "combobox":
        el.click()
        el.fill(str(value)) if el.evaluate("e => e.tagName") == "INPUT" else page.keyboard.type(str(value))
        page.wait_for_timeout(1200)
        options = _options_of(page, el)
        exact = options.filter(has_text=re.compile(rf"^\s*{re.escape(str(value))}\s*$", re.I))
        if exact.count():
            exact.first.click(timeout=5000)
        else:  # type-to-search: accept a suggestion only if it contains what we typed
            near = options.filter(has_text=re.compile(re.escape(str(value)), re.I))
            if not near.count():
                _close(page, el)
                raise LookupError(f"no option matching {value!r}")
            near.first.click(timeout=5000)
    else:
        raise ValueError(f"unsupported kind {k}")


def verify(page, f, value):
    """Best-effort read-back; returns True when the page holds the value."""
    k = f["kind"]
    el = loc(page, f["id"])
    try:
        if k in ("text", "email", "tel", "url", "number", "date", "textarea"):
            for _ in range(3):  # Workday commits on blur/re-render; don't read too eagerly
                try:
                    if (el.input_value() or "").strip():
                        return True
                except Exception:
                    pass
                page.wait_for_timeout(350)
            return False
        if k == "select":
            return el.evaluate("e => e.options[e.selectedIndex]?.text?.trim()") == value
        if k == "checkbox":
            return el.is_checked() == as_bool(value)
        if k in ("radio", "checkbox_group"):
            wanted = value if isinstance(value, list) else [value]
            return all(loc(page, f["optionIds"][f["options"].index(v)]).is_checked() for v in wanted)
        if k == "combobox":
            box = el.locator("xpath=ancestor::*[contains(@class,'select__control')][1]")
            text = box.inner_text() if box.count() else el.input_value()
            return bool(text.strip())
        if k == "wd_select":
            got = el.inner_text().strip()
            return got == value or got.startswith(re.split(r"\s*\(", str(value))[0]) or (
                got != "" and not re.match(r"select one", got, re.I))
        if k == "wd_multiselect":
            box = el.locator("xpath=ancestor::*[starts-with(@data-automation-id,'formField')][1]")
            return box.locator("[data-automation-id='selectedItem']").count() > 0
        return True  # yesno: no reliable generic read-back
    except Exception:
        return False


def captcha_visible(page):
    for sel in CAPTCHA_SELECTORS:
        for fr in page.locator(sel).all():
            try:
                if fr.is_visible():
                    return True
            except Exception:
                pass
    return False


OUTAGE_RX = re.compile(r"currently unavailable|service interruption|something went wrong|temporarily unavailable|under maintenance|try again later|unexpected error", re.I)


def looks_like_outage(text):
    """Corroborate a model page_error verdict: a real outage page is short or
    carries an outage phrase. A full posting that merely got flagged is not."""
    return len(text or "") < 1500 or bool(OUTAGE_RX.search(text or ""))


def page_text(page, limit=20000):
    try:
        return page.inner_text("body")[:limit]
    except Exception:
        return ""


def confirmed(page):
    return bool(CONFIRM_RX.search(page_text(page))) or bool(
        re.search(r"/(confirmation|thanks|thank-you|success)\b", page.url))


def validation_errors(page):
    msgs = page.locator("[aria-invalid=true], [class*='error' i]:visible, [role=alert]").all_inner_texts()
    return [m.strip() for m in msgs if m.strip()][:10]


DATE_PART_RX = re.compile(r"^(.*?)\s*—\s*(month|day|year)\b", re.I)


def _fill_date_groups(page, fields, answers):
    """Workday date = three auto-advancing sections. Typing them one by one
    scrambles the digits, so type MMDDYYYY into the month section at once.
    Returns the set of field ids handled here."""
    groups = {}
    for f in fields:
        m = DATE_PART_RX.match(f["label"] or "")
        if m:
            groups.setdefault(m.group(1), {})[m.group(2).lower()] = f
    handled, bad = set(), []
    for parts in groups.values():
        mon = parts.get("month")
        if not mon:
            continue
        mm = (answers.get(mon["id"]) or {}).get("value")
        dd = (answers.get((parts.get("day") or {}).get("id")) or {}).get("value")
        yy = (answers.get((parts.get("year") or {}).get("id")) or {}).get("value")
        if not (mm and dd and yy):
            continue
        combined = f"{int(mm):02d}{int(dd):02d}{int(yy):04d}"
        el = loc(page, mon["id"])
        for p in parts.values():
            handled.add(p["id"])
        for _ in range(2):
            try:
                el.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            el.evaluate("e => e.focus()")
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            page.keyboard.type(combined, delay=110)
            el.evaluate("e => e.blur()")
            page.wait_for_timeout(400)
            if (el.input_value() or "").strip().zfill(2) == f"{int(mm):02d}":
                break
        else:
            bad.append(mon["label"][:60])
    return handled, bad


def _already(f, value):
    """Field already holds the answer (exact, or a choice field whose current
    text contains it, e.g. 'United States of America (+1)' vs 'United States')."""
    cur = f.get("value")
    if cur in (None, "", []) or isinstance(value, (bool, list)):
        return cur == value
    if cur == value:
        return True
    base = lambda x: re.sub(r"\s*\(.*?\)\s*$", "", str(x)).strip().lower()  # drop "(+1)"
    return f["kind"] in ("wd_select", "wd_multiselect", "select", "combobox") and \
        (base(value) in base(cur) or base(cur) == base(value))


def fill_all(page, fields, answers):
    """Fill every answered field (skipping ones already holding the answer);
    returns the required fields that could not be set."""
    handled, unfilled = _fill_date_groups(page, fields, answers)
    unfilled = list(unfilled)
    for f in fields:
        if f["id"] in handled:
            continue
        a = answers.get(f["id"])
        if not a or _already(f, a["value"]):
            continue
        # el.fill() on a plain text input is reliable; its read-back is flaky on
        # Workday (re-renders drop our element handle), so a clean fill is trusted
        # and Workday's own Save validation catches any truly-empty required field.
        trust_fill = f["kind"] in ("text", "email", "tel", "url", "number", "date", "textarea")
        try:
            fill(page, f, a["value"])
            ok, why = (True if trust_fill else verify(page, f, a["value"])), "did not stick"
        except Exception as e:  # noqa: BLE001 — one bad field shouldn't hide the others
            ok, why = False, type(e).__name__
        if not ok:
            print(f"  could not fill {f['label'][:60]!r}: {why}")
            # a required field that already holds a non-empty value is satisfied
            # — a failed overwrite (e.g. reshuffling equivalent URLs) isn't a block
            if f.get("required") and f.get("value") in (None, "", []):
                unfilled.append(f"{f['label'][:60]} ({why})")
    return unfilled


def enter_security_code(page, code):
    """Type an emailed one-time code into the ATS's code field(s) and press
    its verify/resubmit button. Returns False when no code field is found."""
    boxes = page.locator("input[maxlength='1']:visible")
    if boxes.count() >= len(code):
        for i, ch in enumerate(code):
            boxes.nth(i).fill(ch)
    else:
        target = None
        inputs = page.locator("input:visible:not([type=hidden]):not([type=checkbox]):not([type=radio])")
        for i in range(inputs.count()):
            el = inputs.nth(i)
            meta = el.evaluate("""e => [e.id, e.name, e.placeholder, e.getAttribute('aria-label'),
                (e.labels && e.labels[0] ? e.labels[0].innerText : ''),
                (e.closest('div,fieldset,section')||{}).innerText||''].join(' ').slice(0, 400)""")
            if re.search(r"security|verif|validation|one.?time|\bcode\b", meta, re.I) and not el.input_value():
                target = el
                break
        if target is None:
            return False
        target.fill(code)
    page.wait_for_timeout(500)
    btn = page.get_by_role("button", name=re.compile(r"^\s*(verify|submit|resubmit|confirm|continue)", re.I))
    if not btn.count():
        return False
    btn.last.click(timeout=8000)
    return True
