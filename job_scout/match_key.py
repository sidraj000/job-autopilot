"""Deterministic company/title matching key, shared by applications.json (step 7)
and to_apply.json (step 8) so both sides of the dedup always agree.

Lowercasing + whitespace-collapsing alone isn't enough: the same job title
rendered by two different sources (a Gmail confirmation vs. the original
listing) can differ only in punctuation (comma vs. hyphen, "&" vs "and",
em-dash vs hyphen, etc.) and silently fail to match. Stripping punctuation
down to alphanumerics fixes that.
"""
import re


def normalize(s):
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def key(company, title):
    return f"{normalize(company)}::{normalize(title)}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: match_key.py <company> <title>", file=sys.stderr)
        sys.exit(1)
    print(key(sys.argv[1], sys.argv[2]))
