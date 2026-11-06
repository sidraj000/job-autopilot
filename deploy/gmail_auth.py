#!/usr/bin/env python3
"""One-time: create secrets/gmail_token.json (Gmail READ-ONLY) for the runner.

Run this yourself on a machine with a browser (e.g. your laptop):
  1. Google Cloud console -> APIs & Services -> enable "Gmail API"
  2. Credentials -> Create credentials -> OAuth client ID -> "Desktop app"
     (OAuth consent screen: External, add your Gmail as a test user)
  3. python3 deploy/gmail_auth.py CLIENT_ID CLIENT_SECRET
  4. copy secrets/gmail_token.json to the machine that runs the pipeline
The token can only read mail (scope gmail.readonly). Revoke any time at
https://myaccount.google.com/permissions. Stdlib only.
"""
import http.server
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
OUT = Path(__file__).resolve().parent.parent / "secrets" / "gmail_token.json"


def main():
    if len(sys.argv) == 3:
        client_id, client_secret = sys.argv[1], sys.argv[2]
    else:  # fall back to the repo .env (CLIENT_ID / CLIENT_SECRET or GMAIL_-prefixed)
        env = {}
        envf = OUT.parent.parent / ".env"
        if envf.exists():
            for line in envf.read_text().splitlines():
                k, sep, v = line.partition("=")
                if sep and not k.strip().startswith("#"):
                    env[k.strip()] = v.strip().strip("'\"")
        client_id = env.get("GMAIL_CLIENT_ID") or env.get("CLIENT_ID")
        client_secret = env.get("GMAIL_CLIENT_SECRET") or env.get("CLIENT_SECRET")
        if not (client_id and client_secret):
            sys.exit(__doc__)
    state = secrets.token_urlsafe(16)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if q.get("state") == state:
                got.update(q)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening browser for Google consent (read-only Gmail)...\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if "error" in got:
        sys.exit(f"consent failed: {got['error']}")
    body = urllib.parse.urlencode({"code": got["code"], "client_id": client_id,
                                   "client_secret": client_secret, "redirect_uri": redirect,
                                   "grant_type": "authorization_code"}).encode()
    tok = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=20))
    if "refresh_token" not in tok:
        sys.exit("no refresh_token returned; remove the app at myaccount.google.com/permissions and retry")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"client_id": client_id, "client_secret": client_secret,
                               "refresh_token": tok["refresh_token"]}))
    OUT.chmod(0o600)
    print(f"wrote {OUT} (mode 600). Copy it into secrets/ on the machine that runs the pipeline")


if __name__ == "__main__":
    main()
