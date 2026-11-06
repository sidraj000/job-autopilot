"""Per-ATS differences. Ashby/Greenhouse/Lever/Rippling are single-page;
Workday is multi-step with sign-in (workday.py). Anything else is routed to
the "needs you" list."""
import re
from urllib.parse import urlsplit, urlunsplit

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
    host = urlsplit(url).netloc.lower()
    if "myworkdayjobs" in host or "myworkdaysite" in host:
        return WORKDAY
    for needle, key in (("ashbyhq", "ashby"), ("lever.co", "lever"),
                        ("greenhouse", "greenhouse"), ("rippling", "rippling")):
        if needle in host:
            return ADAPTERS[key]
    return None
