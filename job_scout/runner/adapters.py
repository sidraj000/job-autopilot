"""Per-ATS differences. Ashby/Greenhouse/Lever/Rippling are single-page;
Workday is multi-step with sign-in (workday.py). Anything else is routed to
the "needs you" list."""
import re
from urllib.parse import urlsplit, urlunsplit

from . import config  # noqa: F401 — puts job_scout/ on sys.path
import ats_hosts  # noqa: E402

SUBMIT_RX = re.compile(r"^\s*(submit( your)? application|submit|apply)\s*$", re.I)


def _path_with(url, suffix):
    s = urlsplit(url)
    path = s.path.rstrip("/")
    if not path.endswith(suffix):
        path += suffix
    return urlunsplit((s.scheme, s.netloc, path, s.query, ""))


class Adapter:
    name = "generic"

    def form_url(self, url):
        return url

    def open_form(self, page, url):
        page.goto(self.form_url(url), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

    def submit_button(self, page):
        return page.get_by_role("button", name=SUBMIT_RX).last


class Ashby(Adapter):
    name = "ashby"

    def form_url(self, url):
        return _path_with(url, "/application")


class Lever(Adapter):
    name = "lever"

    def form_url(self, url):
        return _path_with(url, "/apply")


class Greenhouse(Adapter):
    name = "greenhouse"


class Rippling(Adapter):
    name = "rippling"

    def form_url(self, url):
        return _path_with(url, "/apply")


class Workday(Adapter):
    """Marker: the multi-step flow lives in workday.py."""
    name = "workday"


WORKDAY = Workday()
ADAPTERS = {"ashby": Ashby(), "lever": Lever(), "greenhouse": Greenhouse(), "rippling": Rippling()}


def for_url(url):
    """Adapter for a job URL, by exact ATS host (see ats_hosts). Lookalike
    hosts get no adapter, so the runner never fills or signs in on them."""
    name = ats_hosts.ats_for(url)
    if name == "workday":
        return WORKDAY
    return ADAPTERS.get(name)
