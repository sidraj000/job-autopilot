"""Which hosts belong to which ATS (stdlib only).

Matching is exact: a URL is on an ATS only if it is https and its host IS one
of the domains below or a subdomain of one. A substring test ("myworkdayjobs"
in host) would also accept lookalikes such as
acme.myworkdayjobs.com.evil.example or https://acme.myworkdayjobs.com@evil.example,
and the runner types your Workday password into whatever it treats as Workday.
"""
import re
from urllib.parse import urlsplit

ATS_DOMAINS = {
    "workday": ("myworkdayjobs.com", "myworkdaysite.com"),
    "ashby": ("ashbyhq.com",),
    "greenhouse": ("greenhouse.io",),
    "lever": ("lever.co",),
    "rippling": ("rippling.com",),
    "workable": ("workable.com",),
    "smartrecruiters": ("smartrecruiters.com",),
    "jobright": ("jobright.ai",),
}
# A host containing one of these but not on the real domain is a lookalike.
# ("lever.co" only at a label start: clever.com is not a lookalike.)
LOOKALIKE = re.compile(r"myworkday|ashbyhq|greenhouse|(?:^|[.-])lever\.co|rippling|workable|smartrecruiters", re.I)


def hostname(url):
    """Lower-case host of a URL, without port, credentials or trailing dot."""
    try:
        return (urlsplit(url or "").hostname or "").rstrip(".")
    except ValueError:
        return ""


def on_domain(host, domain):
    return host == domain or host.endswith("." + domain)


def ats_for(url):
    """The ATS name for an https URL on a real ATS host, else None."""
    try:
        s = urlsplit(url or "")
    except ValueError:
        return None
    if s.scheme != "https" or "@" in s.netloc:
        return None
    host = hostname(url)
    for name, domains in ATS_DOMAINS.items():
        if any(on_domain(host, d) for d in domains):
            return name
    return None


def suspicious(url):
    """Why a job link should not be followed at all, or None if it's fine.
    Non-ATS employer sites are fine (they go to the "needs you" list)."""
    try:
        s = urlsplit(url or "")
    except ValueError:
        return "unparseable URL"
    if s.scheme != "https":
        return f"not https ({s.scheme or 'no scheme'})"
    if "@" in s.netloc:
        return "credentials in the URL"
    host = hostname(url)
    if not host:
        return "no host"
    if LOOKALIKE.search(host) and not ats_for(url):
        return f"lookalike ATS host {host}"
    return None
