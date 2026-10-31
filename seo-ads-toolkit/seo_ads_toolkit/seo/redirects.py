"""Redirect chain tracer and canonical-variant (http/https/www) checker."""
from __future__ import annotations

import re
import urllib.parse

from ..core import (Tool, ToolError, add_net_args, ensure_scheme, fetch, issue, parse_html,
                    read_lines, sort_issues)


def trace(url, timeout=15.0, ua=None):
    from ..core import DEFAULT_UA
    r = fetch(url, timeout=timeout, ua=ua or DEFAULT_UA, follow=True, max_redirects=15)
    hops = [{"step": i + 1, "url": u, "status": s} for i, (u, s) in enumerate(r.chain)]
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    redirects = [h for h in hops if 300 <= h["status"] < 400]
    if r.error == "redirect loop":
        add("error", "REDIRECT_LOOP", "Redirect loop detected.")
    elif r.error == "too many redirects":
        add("error", "TOO_MANY_REDIRECTS", "More than 15 redirects.")
    elif r.status == 0:
        add("error", "UNREACHABLE", "Request failed: %s" % r.error)
    if len(redirects) > 1:
        add("warning", "REDIRECT_CHAIN", "%d redirects in a row; point links straight at the final URL." % len(redirects))
    for h in redirects:
        if h["status"] in (302, 303, 307):
            add("info", "TEMPORARY_REDIRECT", "Step %d is a %d (temporary). Use 301/308 for permanent moves." % (h["step"], h["status"]))
    if r.status >= 400:
        add("error", "FINAL_ERROR", "Final URL returns %d." % r.status)
    if hops and r.ok and r.is_html:
        page = parse_html(r.text, r.final_url)
        if page.refresh:
            add("warning", "META_REFRESH", "Final page uses a meta refresh redirect: %s" % page.refresh)
    if urllib.parse.urlsplit(url).scheme == "https" and urllib.parse.urlsplit(r.final_url).scheme == "http":
        add("error", "HTTPS_DOWNGRADE", "https URL ends up on http.")
    return {"start": url, "final_url": r.final_url, "final_status": r.status, "redirects": len(redirects),
            "total_ms": int(r.elapsed * 1000), "hops": hops, "issues": sort_issues(issues)}


def variants(host, timeout=15.0, ua=None):
    host = re.sub(r"^https?://", "", host).strip("/").split("/")[0]
    bare = host[4:] if host.startswith("www.") else host
    cands = ["http://" + bare + "/", "https://" + bare + "/", "http://www." + bare + "/", "https://www." + bare + "/"]
    rows, finals = [], set()
    for c in cands:
        t = trace(c, timeout, ua)
        rows.append({"variant": c, "final_url": t["final_url"], "status": t["final_status"], "redirects": t["redirects"]})
        if t["final_status"] == 200:
            finals.add(t["final_url"])
    issues = []
    if len(finals) > 1:
        issues.append(issue("error", "MULTIPLE_VERSIONS", "%d different URLs serve the homepage with 200: %s" % (len(finals), ", ".join(sorted(finals)))))
    elif len(finals) == 1:
        issues.append(issue("info", "SINGLE_VERSION", "All variants resolve to %s" % next(iter(finals))))
    for r in rows:
        if r["redirects"] > 1:
            issues.append(issue("warning", "VARIANT_CHAIN", "%s takes %d redirects." % (r["variant"], r["redirects"])))
    return {"host": host, "variants": rows, "issues": sort_issues(issues)}


def _args(p):
    p.add_argument("urls", nargs="*", help="URLs to trace")
    p.add_argument("--file", help="file with one URL per line")
    p.add_argument("--variants", metavar="DOMAIN", help="check http/https x www/non-www versions of a domain")
    add_net_args(p)


def _run(a):
    if a.variants:
        return variants(a.variants, a.timeout, a.ua)
    urls = list(a.urls) + (read_lines(a.file) if a.file else [])
    if not urls:
        raise ToolError("give URLs, --file, or --variants DOMAIN")
    traces = [trace(ensure_scheme(u), a.timeout, a.ua) for u in urls]
    if len(traces) == 1:
        return traces[0]
    rows = [{"start": t["start"], "final_url": t["final_url"], "status": t["final_status"], "redirects": t["redirects"]} for t in traces]
    issues = [dict(i, message="%s: %s" % (t["start"], i["message"])) for t in traces for i in t["issues"]]
    return {"summary": rows, "issues": sort_issues(issues)}


TOOLS = [Tool("redirects", "SEO / Technical", "Trace redirect chains/loops; check http/https/www canonical versions", _args, _run)]
