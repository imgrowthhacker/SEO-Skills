"""ads.txt / app-ads.txt validator."""
from __future__ import annotations

import os
import re
from collections import Counter

from ..core import (Tool, ToolError, add_net_args, ensure_scheme, fetch, issue, origin_of,
                    read_text_file, sort_issues)

VARIABLES = {"contact", "subdomain", "inventorypartnerdomain", "ownerdomain", "managerdomain"}
# Certification authority IDs published by these systems for their ads.txt lines.
KNOWN_CERT = {"google.com": "f08c47fec0942fa0", "appnexus.com": "f5ab79cb980f11d1", "rubiconproject.com": "0bfd66d529a55807",
              "openx.com": "6a698e2ec38604c6", "pubmatic.com": "5d62403b186f2ace"}
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def parse(text):
    records, variables, issues = [], [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line and "," not in line.split("=", 1)[0]:
            k, v = [x.strip() for x in line.split("=", 1)]
            if k.lower() in VARIABLES:
                variables.append((k.upper(), v))
            else:
                issues.append(issue("warning", "UNKNOWN_VARIABLE", "line %d: unknown variable %r" % (n, k)))
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            issues.append(issue("error", "BAD_FIELDS", "line %d: expected 'domain, account id, DIRECT|RESELLER[, cert id]'" % n))
            continue
        records.append({"line": n, "domain": parts[0].lower(), "account_id": parts[1], "relationship": parts[2].upper(),
                        "cert_id": parts[3].lower() if len(parts) > 3 else ""})
    return records, variables, issues


def validate(text):
    records, variables, issues = parse(text)
    add = lambda *a, **k: issues.append(issue(*a, **k))
    if not records:
        add("error", "NO_RECORDS", "No valid seller records found.")
    seen = Counter()
    for r in records:
        key = (r["domain"], r["account_id"], r["relationship"])
        seen[key] += 1
        if not DOMAIN.match(r["domain"]):
            add("error", "BAD_DOMAIN", "line %d: %r is not a valid advertising-system domain." % (r["line"], r["domain"]))
        if not r["account_id"]:
            add("error", "EMPTY_ACCOUNT", "line %d: empty account ID." % r["line"])
        if r["relationship"] not in ("DIRECT", "RESELLER"):
            add("error", "BAD_RELATIONSHIP", "line %d: relationship %r must be DIRECT or RESELLER." % (r["line"], r["relationship"]))
        if r["domain"] == "google.com" and not r["account_id"].startswith("pub-"):
            add("warning", "GOOGLE_PUB_ID", "line %d: Google publisher IDs start with 'pub-'." % r["line"])
        exp = KNOWN_CERT.get(r["domain"])
        if r["cert_id"] and not re.fullmatch(r"[0-9a-f]{9,16}", r["cert_id"]):
            add("warning", "CERT_FORMAT", "line %d: certification authority ID %r looks malformed." % (r["line"], r["cert_id"]))
        elif exp and r["cert_id"] and r["cert_id"] != exp:
            add("warning", "CERT_MISMATCH", "line %d: %s usually publishes cert ID %s, found %s." % (r["line"], r["domain"], exp, r["cert_id"]))
        elif exp and not r["cert_id"]:
            add("info", "CERT_MISSING", "line %d: %s lines normally carry cert ID %s." % (r["line"], r["domain"], exp))
    for k, c in seen.items():
        if c > 1:
            add("warning", "DUPLICATE", "%s, %s, %s appears %d times." % (k[0], k[1], k[2], c))
    if not any(k == "OWNERDOMAIN" for k, _ in variables) and records:
        add("info", "NO_OWNERDOMAIN", "No OWNERDOMAIN variable (recommended in ads.txt 1.1).")
    by = Counter(r["domain"] for r in records)
    return {"records": len(records), "direct": sum(1 for r in records if r["relationship"] == "DIRECT"),
            "reseller": sum(1 for r in records if r["relationship"] == "RESELLER"),
            "variables": [{"name": k, "value": v} for k, v in variables],
            "by_ad_system": [{"ad_system": d, "lines": c} for d, c in by.most_common(15)],
            "issues": sort_issues(issues)}


def _args(p):
    p.add_argument("target", help="domain/URL to fetch ads.txt from, or a local file")
    p.add_argument("--app", action="store_true", help="fetch app-ads.txt instead")
    add_net_args(p)


def _run(a):
    name = "app-ads.txt" if a.app else "ads.txt"
    if os.path.isfile(a.target):
        text, src, status = read_text_file(a.target), a.target, None
    else:
        t = a.target if a.target.lower().rstrip("/").endswith("ads.txt") else origin_of(ensure_scheme(a.target)) + "/" + name
        r = fetch(t, timeout=a.timeout, ua=a.ua)
        if r.status == 0:
            raise ToolError("could not fetch %s: %s" % (t, r.error))
        if r.status != 200:
            return {"source": r.final_url, "http_status": r.status, "issues": [issue("error", "NOT_FOUND", "%s returned HTTP %d." % (name, r.status))]}
        text, src, status = r.text, r.final_url, r.status
        if "<html" in text[:500].lower():
            return {"source": src, "http_status": status, "issues": [issue("error", "HTML_RESPONSE", "Server returned an HTML page instead of a text file (soft-404?).")]}
    res = validate(text)
    return dict({"source": src, "http_status": status}, **res)


TOOLS = [Tool("adstxt", "Ads / Copy, tracking & landing pages", "Fetch + validate ads.txt / app-ads.txt (syntax, duplicates, cert IDs)", _args, _run)]
