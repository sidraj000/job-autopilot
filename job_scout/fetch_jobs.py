#!/usr/bin/env python3
"""Job fetcher — pulls public job listings into data/raw/latest.json.

Sources (all public, no auth):
  - Greenhouse boards API   https://boards-api.greenhouse.io/v1/boards/{token}/jobs
  - Ashby job-board API     https://api.ashbyhq.com/posting-api/job-board/{org}
  - Lever postings API      https://api.lever.co/v0/postings/{org}?mode=json
  - GitHub job-tracker repos, markdown-pipe-table style (speedyapply, vanshb03, ...)
  - GitHub job-tracker repos, HTML-table style (SimplifyJobs/New-Grad-Positions, ...)
  - Jobright public search pages, resolved to the employer's own ATS posting
  - Jobright personalised recommendations (your session in secrets/, optional)

Which boards/pages, and the title/location filters, come from
profile/search.json. Jobs already recorded in data/seen.json (a prior run) are dropped before
writing output, so repeat runs only surface genuinely new listings.

Stdlib only. Failures per-source are tolerated and reported, never fatal.
"""
import json
import re
import sys
import urllib.request
from datetime import date

import paths
import seen_store

CFG = paths.search_config()
OUT_DIR = paths.DATA_DIR / "raw"
MAX_YEARS = CFG.get("max_years_experience", 3)
UA = {"User-Agent": "Mozilla/5.0 (job-autopilot personal script)"}

INCLUDE = re.compile(CFG["title_include_regex"])
EXCLUDE = re.compile(CFG["title_exclude_regex"])
US_LOC = re.compile(CFG["us_location_regex"])
EXCLUDED_COMPANIES = {c.strip().lower() for c in CFG.get("excluded_companies", [])}


def get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def keep(title, location, company=""):
    # whole-word match, not equality — "TikTok USDS" / "TikTok Inc." must
    # count as TikTok
    c = (company or "").strip().lower()
    if any(re.search(rf"\b{re.escape(x)}\b", c) for x in EXCLUDED_COMPANIES):
        return False
    if not INCLUDE.search(title or ""):
        return False
    if EXCLUDE.search(title or ""):
        return False
    loc = (location or "").strip()
    # empty or bare "Remote" (no country/region qualifier): pass it through and
    # let the scoring step judge logistics — the tracker sources are US-scoped,
    # and a qualified non-US remote ("Remote - Canada") still fails below
    if not loc or loc.lower() == "remote":
        return True
    return bool(US_LOC.search(loc))


NO_SPONSOR_EMOJI = "\U0001F6C2"  # 🛂 — shared convention: "does NOT offer sponsorship"


def sponsorship_from_text(*texts):
    """H1B sponsorship is not exposed by any structured API here — only the
    community trackers annotate it (via the 🛂 emoji convention). We can only
    ever confirm "no"; absence of the marker doesn't mean sponsorship is offered."""
    return "no" if any(NO_SPONSOR_EMOJI in (t or "") for t in texts) else "unknown"


def red_flags_for(h1b_sponsorship):
    """Deterministic red flags derivable at fetch time. The scoring agent
    appends to this list (e.g. "requires_phd") — never removes from it, and
    never lets these lower the score (see job_scout/profile.md)."""
    return ["no_sponsorship"] if h1b_sponsorship == "no" else []


GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{}/jobs"
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{}"
LEVER_URL = "https://api.lever.co/v0/postings/{}?mode=json"


def company_name(token):
    return CFG.get("company_names", {}).get(token, token)


def fetch_greenhouse(token):
    data = json.loads(get(GREENHOUSE_URL.format(token)))
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if keep(j.get("title", ""), loc, token):
            out.append({"source": f"greenhouse/{token}", "company": company_name(token),
                        "title": j["title"], "location": loc,
                        "url": j.get("absolute_url", ""), "h1b_sponsorship": "unknown",
                        "red_flags": red_flags_for("unknown")})
    return out


def fetch_ashby(org):
    data = json.loads(get(ASHBY_URL.format(org)))
    out = []
    for j in data.get("jobs", []):
        # the bare "location" is just a city ("San Francisco") which the US
        # filter can't judge — fold in the region/country from the address
        addr = (j.get("address") or {}).get("postalAddress") or {}
        loc = ", ".join(p for p in (j.get("location", ""),
                                    addr.get("addressRegion", ""),
                                    addr.get("addressCountry", "")) if p)
        if j.get("isRemote"):
            loc = f"Remote — {loc}" if loc else "Remote"
        if keep(j.get("title", ""), loc, org):
            out.append({"source": f"ashby/{org}", "company": company_name(org),
                        "title": j["title"], "location": loc,
                        "url": j.get("jobUrl", ""), "h1b_sponsorship": "unknown",
                        "red_flags": red_flags_for("unknown")})
    return out


def fetch_lever(org):
    data = json.loads(get(LEVER_URL.format(org)))
    out = []
    for j in data:
        loc = (j.get("categories") or {}).get("location", "")
        if keep(j.get("text", ""), loc, org):
            out.append({"source": f"lever/{org}", "company": company_name(org),
                        "title": j["text"], "location": loc,
                        "url": j.get("hostedUrl", ""), "h1b_sponsorship": "unknown",
                        "red_flags": red_flags_for("unknown")})
    return out


NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _slugs(company):
    base = re.sub(r"\b(inc|llc|ltd|corp(oration)?|co|technologies|labs?|ai|group)\b\.?", "", company.lower())
    words = re.findall(r"[a-z0-9]+", base) or re.findall(r"[a-z0-9]+", company.lower())
    full = re.findall(r"[a-z0-9]+", company.lower())
    out = []
    for w in ("".join(words), "-".join(words), "".join(full), "-".join(full), words[0] if words else ""):
        if w and w not in out:
            out.append(w)
    return out


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def resolve_on_board(company, title):
    """Jobright hides the employer link behind a login, so look the same
    posting up on the company's own public Greenhouse/Ashby/Lever board.
    Returns the ATS posting url, or None when no exact-title match exists."""
    want = _norm_title(title)
    for slug in _slugs(company):
        for url, rows, t_key, u_key in (
                (GREENHOUSE_URL.format(slug), lambda d: d.get("jobs", []), "title", "absolute_url"),
                (ASHBY_URL.format(slug), lambda d: d.get("jobs", []), "title", "jobUrl"),
                (LEVER_URL.format(slug), lambda d: d if isinstance(d, list) else [], "text", "hostedUrl")):
            try:
                data = json.loads(get(url, timeout=12))
            except Exception:  # noqa: BLE001 — 404 = not this ATS/slug
                continue
            for j in rows(data):
                if _norm_title(j.get(t_key)) == want and j.get(u_key):
                    if t_key == "title" and "greenhouse" in url and j.get("id"):
                        # canonical board url — absolute_url is often a custom careers domain
                        return f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}"
                    return j[u_key]
    return None


def fetch_jobright(url):
    """Jobright public search page: listings come from the page's __NEXT_DATA__;
    each kept one is resolved to its employer ATS posting (unresolvable ones
    are dropped — the runner can't apply through Jobright without a login)."""
    data = json.loads(NEXT_DATA.search(get(url)).group(1))
    out = []
    for item in data["props"]["pageProps"].get("jobList", []):
        j, c = item.get("jobResult") or {}, item.get("companyResult") or {}
        company, title = c.get("companyName", ""), j.get("jobTitle", "")
        if (j.get("minYearsOfExperience") or 0) > MAX_YEARS or not keep(title, j.get("jobLocation", ""), company):
            continue
        ats = resolve_on_board(company, title)
        if ats:
            out.append({"source": "jobright", "company": company, "title": title,
                        "location": j.get("jobLocation", ""), "url": ats,
                        "h1b_sponsorship": "unknown", "red_flags": red_flags_for("unknown")})
    return out


JOBRIGHT_SESSION = paths.SECRETS_DIR / "jobright_session.txt"
# reposting sites, not the employer's own form (often duplicates of jobs we already have)
AGGREGATORS = re.compile(r"linkedin\.com|jobright\.ai|dice\.com|ziprecruiter\.|indeed\.|glassdoor\.|monster\.com|simplyhired|talent\.com|builtin\.com", re.I)
JOBRIGHT_RECS = "https://jobright.ai/swan/recommend/list/jobs?refresh=false&sortCondition=0&position={}&count=20"


def employer_url(u):
    """Normalise an ATS link: Greenhouse embed form -> its public posting."""
    m = re.search(r"greenhouse\.io/embed/job_app\?(?=.*\bfor=([\w-]+))(?=.*\btoken=(\d+))", u or "")
    if m:
        return f"https://job-boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}"
    return (u or "").replace("?source=jobright", "").rstrip("?")


def fetch_jobright_recs(pages=10):
    """Your personalised Jobright recommendations (your own logged-in session:
    secrets/jobright_session.txt holding `SESSION_ID=...`, never committed).
    Uses the employer link Jobright shows signed-in users; LinkedIn-only and
    aggregator listings are dropped (no employer form to fill)."""
    cookie = JOBRIGHT_SESSION.read_text().strip()
    out, ids = [], set()
    for page in range(pages):
        req = urllib.request.Request(JOBRIGHT_RECS.format(page * 20), headers={
            **UA, "Cookie": cookie, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        if not data.get("success"):
            raise RuntimeError(f"session rejected ({data.get('errorMsg') or data.get('errorCode')}) — re-export SESSION_ID")
        rows = (data.get("result") or {}).get("jobList") or []
        if not rows:
            break
        for item in rows:
            j, c = item.get("jobResult") or {}, item.get("companyResult") or {}
            if j.get("jobId") in ids:
                continue
            ids.add(j.get("jobId"))
            company, title = c.get("companyName", ""), j.get("jobTitle", "")
            url = employer_url(j.get("originalUrl") or j.get("applyLink"))
            if (not url.startswith("http") or AGGREGATORS.search(url)
                    or (j.get("minYearsOfExperience") or 0) > MAX_YEARS
                    or not keep(title, j.get("jobLocation", ""), company)):
                continue
            out.append({"source": "jobright_recs", "company": company, "title": title,
                        "location": j.get("jobLocation", ""), "url": url,
                        "h1b_sponsorship": "unknown", "red_flags": red_flags_for("unknown")})
    return out


MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
HTML_LINK = re.compile(r'<a\s+href="(https?://[^"]+)"')
TAG = re.compile(r"<[^>]+>")


def strip_tags(s):
    return re.sub(r"\s+", " ", TAG.sub(" ", s)).strip()


def clean_cell(s):
    """Reduce a tracker table cell to plain text: resolve markdown links to
    their text (never blanket-strip parens — titles like "Research Engineer
    (Machine Learning)" must keep the part the filters match on), then drop
    leftover markdown emphasis/brackets and HTML tags."""
    s = MD_LINK.sub(r"\1", s)
    s = re.sub(r"[\[\]*]", "", s)
    return strip_tags(s)


def fetch_tracker(url):
    """Parse markdown pipe tables: | company | role | location | ... | link |
    Link may be a markdown [text](url) or an HTML <a href="url"> (vanshb03-style)."""
    out = []
    for line in get(url).splitlines():
        if not line.startswith("|") or line.count("|") < 4:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0].lower() in ("company", ":-", "---", ""):
            continue
        company = clean_cell(cells[0])
        title_cell, loc = cells[1], cells[2]
        # search only the non-company cells for the apply link — the company
        # name itself is often also a hyperlink (to the company homepage),
        # which would otherwise get picked up instead of the real posting link
        rest = "|".join(cells[1:])
        links = [(m.start(), m.group(2)) for m in MD_LINK.finditer(rest)]
        links += [(m.start(), m.group(1)) for m in HTML_LINK.finditer(rest)]
        link = max(links)[1] if links else ""
        title = clean_cell(title_cell)
        loc = strip_tags(loc)
        if company and title and keep(title, loc, company):
            # 🛂 may sit in its own column rather than the title cell — scan the row
            h1b = sponsorship_from_text(line)
            out.append({"source": "tracker", "company": company, "title": title,
                        "location": loc, "url": link,
                        "h1b_sponsorship": h1b, "red_flags": red_flags_for(h1b)})
    return out


ROW = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
CELL = re.compile(r"<td>(.*?)</td>", re.DOTALL)


def fetch_html_table(url):
    """Parse HTML-table job lists (SimplifyJobs-style):
    <tr><td>company</td><td>role</td><td>location</td><td><a href=...>apply</a></td>...</tr>
    Rows for a second role at the same company repeat the company cell as "↳"
    (ditto) instead of the name — carry the last real company name forward.
    A 🔒 in the application cell means the listing is closed — skip it."""
    out = []
    html = get(url)
    last_company = ""
    for row in ROW.findall(html):
        cells = CELL.findall(row)
        if len(cells) < 4:
            continue
        company = strip_tags(cells[0]).lstrip("\U0001F525 ")  # drop leading 🔥 (FAANG+ marker)
        if company in ("", "↳"):
            company = last_company
        else:
            last_company = company
        if "\U0001F512" in cells[3]:  # 🔒 closed
            continue
        title = strip_tags(cells[1])
        loc = strip_tags(re.sub(r"(?i)<\s*/?\s*br\s*/?\s*>", ", ", cells[2]))
        m = HTML_LINK.search(cells[3])
        link = m.group(1) if m else ""
        if company and title and link and keep(title, loc, company):
            # 🛂 may sit in its own column rather than the title cell — scan the row
            h1b = sponsorship_from_text(row)
            out.append({"source": "html_table", "company": company, "title": title,
                        "location": loc, "url": link,
                        "h1b_sponsorship": h1b, "red_flags": red_flags_for(h1b)})
    return out


def main():
    results, report = [], []
    for tok in CFG.get("greenhouse_boards", []):
        try:
            got = fetch_greenhouse(tok)
            results += got
            report.append(f"greenhouse/{tok}: {len(got)}")
        except Exception as e:
            # include the API URL so a blocked-egress run can WebFetch it instead
            report.append(f"greenhouse/{tok}: FAILED ({e}) [{GREENHOUSE_URL.format(tok)}]")
    for org in CFG.get("ashby_orgs", []):
        try:
            got = fetch_ashby(org)
            results += got
            report.append(f"ashby/{org}: {len(got)}")
        except Exception as e:
            report.append(f"ashby/{org}: FAILED ({e}) [{ASHBY_URL.format(org)}]")
    for org in CFG.get("lever_orgs", []):
        try:
            got = fetch_lever(org)
            results += got
            report.append(f"lever/{org}: {len(got)}")
        except Exception as e:
            report.append(f"lever/{org}: FAILED ({e}) [{LEVER_URL.format(org)}]")
    for url in CFG.get("tracker_pages", []):
        try:
            got = fetch_tracker(url)
            results += got
            report.append(f"tracker {url.split('/')[-3]}: {len(got)}")
        except Exception as e:
            report.append(f"tracker {url}: FAILED ({e})")
    for url in CFG.get("html_table_pages", []):
        try:
            got = fetch_html_table(url)
            results += got
            report.append(f"html_table {url.split('/')[-3]}: {len(got)}")
        except Exception as e:
            report.append(f"html_table {url}: FAILED ({e})")

    if JOBRIGHT_SESSION.exists():
        try:
            got = fetch_jobright_recs()
            results += got
            report.append(f"jobright recommendations (your account): {len(got)}")
        except Exception as e:
            report.append(f"jobright recommendations: FAILED ({e})")
    for url in CFG.get("jobright_public_pages", []):
        try:
            got = fetch_jobright(url)
            results += got
            report.append(f"jobright {url.rsplit('/', 1)[-1]}: {len(got)}")
        except Exception as e:
            report.append(f"jobright {url}: FAILED ({e})")

    # dedupe by url (or company+title when url missing)
    seen_keys, deduped = set(), []
    for r in results:
        key = r["url"] or (r["company"] + "::" + r["title"])
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(r)

    # drop anything already processed in a previous run
    seen_store_data = seen_store.load()
    fresh = [r for r in deduped if not seen_store.is_seen(r, seen_store_data)]
    already_seen = len(deduped) - len(fresh)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fetched": date.today().isoformat(), "report": report, "jobs": fresh}
    (OUT_DIR / "latest.json").write_text(json.dumps(payload, indent=1))
    (OUT_DIR / f"{date.today().isoformat()}.json").write_text(json.dumps(payload, indent=1))
    print("\n".join(report))
    print(f"TOTAL after filter+dedupe: {len(deduped)} ({already_seen} already seen, {len(fresh)} new)")


if __name__ == "__main__":
    sys.exit(main())
