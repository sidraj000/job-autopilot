"""Shared "this job was applied to" bookkeeping (stdlib only):
upsert data/applications.json (status "applied", idempotent) and remove the
job from data/to_apply.json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match_key import key as match_key  # noqa: E402
from paths import DATA_DIR  # noqa: E402


def norm_url(u):
    u = u.split("#")[0].split("?")[0].rstrip("/")
    if u.endswith("/application"):
        u = u[: -len("/application")]
    return u.lower()


def record_applied(records):
    """records: {normalized_url: {"url", "company", "title", "date", "via"}}.

    Returns (imported, pruned)."""
    apps_path, ta_path = DATA_DIR / "applications.json", DATA_DIR / "to_apply.json"
    apps = json.loads(apps_path.read_text()) if apps_path.exists() else {}
    ta = json.loads(ta_path.read_text()) if ta_path.exists() else []

    imported = 0
    for nu, rec in records.items():
        # prefer the queue entry's clean company/title over page-title fallbacks
        q = next((j for j in ta if norm_url(j.get("url", "")) == nu), None)
        company = (q or rec)["company"]
        title = (q or rec)["title"]
        if not title:
            print(f"skipped (no title, mark it manually): {rec.get('url')}")
            continue
        k = match_key(company, title)
        hist = {"date": rec["date"], "status": "applied",
                "subject": f"submitted via {rec.get('via', 'unknown')}"}
        if k not in apps:
            apps[k] = {"company": company, "title": title, "url": rec.get("url", ""),
                       "first_applied_or_seen": rec["date"],
                       "current_status": "applied", "status_history": [hist]}
            imported += 1
        elif hist not in apps[k]["status_history"]:
            apps[k]["status_history"].append(hist)
            imported += 1

    applied_urls = set(records)
    applied_keys = {match_key(v["company"], v["title"]) for v in apps.values()}
    kept = [j for j in ta if norm_url(j.get("url", "")) not in applied_urls
            and match_key(j["company"], j["title"]) not in applied_keys]
    pruned = len(ta) - len(kept)

    apps_path.write_text(json.dumps(dict(sorted(apps.items())), indent=1))
    ta_path.write_text(json.dumps(kept, indent=1))
    return imported, pruned
