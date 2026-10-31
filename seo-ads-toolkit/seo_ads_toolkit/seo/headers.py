"""HTTP response header audit: indexing directives, caching, compression, security."""
from __future__ import annotations

import re

from ..core import Tool, ToolError, add_net_args, fetch, issue, sort_issues

SECURITY = [
    ("strict-transport-security", "HSTS", "warning", "Add Strict-Transport-Security so browsers always use https."),
    ("x-content-type-options", "X-Content-Type-Options", "info", "Add 'X-Content-Type-Options: nosniff'."),
    ("content-security-policy", "Content-Security-Policy", "info", "A CSP limits XSS impact; start in report-only mode."),
    ("referrer-policy", "Referrer-Policy", "info", "Set e.g. 'strict-origin-when-cross-origin'."),
    ("x-frame-options", "X-Frame-Options", "info", "Add X-Frame-Options or CSP frame-ancestors to prevent clickjacking."),
]


def analyze_headers(resp):
    h = resp.headers
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    if resp.status == 0:
        raise ToolError("request failed: %s" % resp.error)
    if resp.status >= 500:
        add("error", "SERVER_ERROR", "HTTP %d." % resp.status)
    elif resp.status >= 400:
        add("error", "CLIENT_ERROR", "HTTP %d." % resp.status)
    xrt = h.get("x-robots-tag", "")
    if re.search(r"noindex|none", xrt, re.I):
        add("error", "X_ROBOTS_NOINDEX", "X-Robots-Tag blocks indexing: %s" % xrt)
    elif xrt:
        add("info", "X_ROBOTS_TAG", "X-Robots-Tag present: %s" % xrt)
    enc = h.get("content-encoding", "").lower()
    ct = resp.content_type
    if ct.startswith("text/") or "json" in ct or "xml" in ct:
        if enc not in ("gzip", "br", "deflate", "zstd"):
            add("warning", "NO_COMPRESSION", "Text response is not compressed (no gzip/br).")
    if not h.get("content-type"):
        add("warning", "NO_CONTENT_TYPE", "Missing Content-Type header.")
    elif "charset" not in h["content-type"].lower() and ct == "text/html":
        add("info", "NO_CHARSET_HEADER", "Content-Type has no charset (a <meta charset> must then appear early in the HTML).")
    cc = h.get("cache-control", "")
    if not cc and not h.get("expires"):
        add("info", "NO_CACHE_HEADERS", "No Cache-Control/Expires headers.")
    elif re.search(r"no-store", cc, re.I) and ct == "text/html":
        add("info", "NO_STORE", "HTML sent with no-store: disables back/forward cache.")
    if resp.final_url.startswith("https://"):
        for key, name, sev, msg in SECURITY:
            if key not in h:
                add(sev, "MISSING_" + name.upper().replace("-", "_"), "%s missing. %s" % (name, msg))
    server = h.get("server", "")
    if re.search(r"/\d", server) or h.get("x-powered-by"):
        add("info", "VERSION_DISCLOSURE", "Server/X-Powered-By discloses software: %s %s" % (server, h.get("x-powered-by", "")))
    link = h.get("link", "")
    canon = re.search(r"<([^>]+)>\s*;\s*rel=\"?canonical", link, re.I)
    if resp.ttfb > 0.8:
        add("warning", "SLOW_TTFB", "Time to first byte %.0f ms (aim for < 800 ms)." % (resp.ttfb * 1000))
    elif resp.ttfb > 0.4:
        add("info", "TTFB_MODERATE", "Time to first byte %.0f ms." % (resp.ttfb * 1000))
    return {
        "url": resp.final_url, "status": resp.status, "content_type": ct, "ttfb_ms": int(resp.ttfb * 1000),
        "compression": enc or "none", "cache_control": cc or None, "x_robots_tag": xrt or None,
        "link_canonical": canon.group(1) if canon else None, "server": server or None,
        "size_bytes": len(resp.body), "headers": [{"header": k, "value": v[:120]} for k, v in sorted(h.items())],
        "issues": sort_issues(issues),
    }


def _args(p):
    p.add_argument("url")
    add_net_args(p)


def _run(a):
    resp = fetch(a.url, timeout=a.timeout, ua=a.ua, headers={"Accept": "text/html,*/*"})
    r = analyze_headers(resp)
    if len(resp.chain) > 1:
        r["redirected_from"] = resp.url
    return r


TOOLS = [Tool("headers", "SEO / Technical", "Audit HTTP headers: X-Robots-Tag, compression, caching, TTFB, security headers", _args, _run)]
