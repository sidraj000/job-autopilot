"""Read-only Gmail access for account-verification emails (stdlib only).

Token file (GMAIL_TOKEN_FILE, default secrets/gmail_token.json), created once
by deploy/gmail_auth.py:
  {"client_id": ..., "client_secret": ..., "refresh_token": ...}
Scope is gmail.readonly: this code can find a verification link or code,
never send, delete, or change mail.
"""
import base64
import json
import re
import time
import urllib.parse
import urllib.request

from . import config

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _token_file():
    p = config.ROOT / config.get("GMAIL_TOKEN_FILE", "secrets/gmail_token.json")
    if not p.exists():
        raise FileNotFoundError(f"Gmail token missing: {p} (run deploy/gmail_auth.py)")
    return p


def _access_token():
    t = json.loads(_token_file().read_text())
    body = urllib.parse.urlencode({"client_id": t["client_id"], "client_secret": t["client_secret"],
                                   "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=20) as r:
        return json.load(r)["access_token"]


def _get(path, token, **params):
    url = f"{API}/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _bodies(part):
    if part.get("body", {}).get("data"):
        yield base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    for sub in part.get("parts", []) or []:
        yield from _bodies(sub)


def wait_for_message(query, since_epoch, timeout=300, poll=15):
    """Newest message matching the Gmail query received after since_epoch;
    returns its decoded text (html + plain) or None on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        token = _access_token()
        res = _get("messages", token, q=f"{query} after:{int(since_epoch) - 60}", maxResults=5)
        for m in res.get("messages", []):
            msg = _get(f"messages/{m['id']}", token, format="full")
            if int(msg.get("internalDate", 0)) / 1000 >= since_epoch - 60:
                return "\n".join(_bodies(msg["payload"]))
        time.sleep(poll)
    return None


def count(query):
    return int(_get("messages", _access_token(), q=query, maxResults=1).get("resultSizeEstimate", 0))


def find_link(text, host_hint):
    """First link in the email pointing at the Workday tenant (verification)."""
    links = re.findall(r'https?://[^\s"\'<>]+', text or "")
    links = [l.replace("&amp;", "&") for l in links]
    for l in links:
        if host_hint in l and re.search(r"verif|activat|confirm|token", l, re.I):
            return l
    return next((l for l in links if host_hint in l), None)


def find_code(text):
    m = re.search(r"\b(\d{6})\b", re.sub(r"<[^>]+>", " ", text or ""))
    return m.group(1) if m else None


def find_security_code(text):
    """The one-time code in an ATS "confirm it's you" email: Greenhouse
    ("...field on your application: 12345678"), Clinch-hosted careers sites
    ("...code required to complete your form on the <X> careers website. AB12C")."""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or ""))
    for rx in (r"on your application:\s*([A-Za-z0-9]{4,12})\b",
               r"code required to complete your form[^.]*\.\s*([A-Za-z0-9]{4,12})\b",
               r"\b(?:security|verification|validation|one-time) code(?: is)?\s*[:\-]\s*([A-Za-z0-9]{4,12})\b"):
        m = re.search(rx, t, re.I)
        if m:
            return m.group(1)
    return None
