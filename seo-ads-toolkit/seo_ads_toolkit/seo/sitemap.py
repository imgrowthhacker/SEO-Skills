"""XML sitemap parser, validator and generator."""
from __future__ import annotations

import datetime
import gzip
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from ..core import (Tool, ToolError, add_net_args, check_urls, decode_body, fetch, host_of,
                    issue, read_lines, sort_issues)

MAX_URLS = 50000
MAX_BYTES = 50 * 1024 * 1024
_LASTMOD = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?)?)?$")
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(raw):
    """raw: bytes or str. Returns {"type": urlset|index|text, "entries": [...]}."""
    if isinstance(raw, bytes):
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        text = decode_body(raw)
    else:
        text = raw
    stripped = text.lstrip("﻿ \r\n\t")
    if not stripped.startswith("<"):
        urls = [l.strip() for l in text.splitlines() if l.strip()]
        return {"type": "text", "entries": [{"loc": u} for u in urls]}
    if re.search(r"<!DOCTYPE|<!ENTITY", stripped[:4096], re.I):
        raise ToolError("sitemap contains a DOCTYPE/ENTITY declaration; refusing to parse (unsafe XML).")
    try:
        root = ET.fromstring(stripped.encode("utf-8"))
    except ET.ParseError as e:
        raise ToolError("sitemap is not well-formed XML: %s" % e)
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise ToolError("root element <%s> is not urlset or sitemapindex" % kind)
    entries = []
    for child in root:
        if _local(child.tag) not in ("url", "sitemap"):
            continue
        e = {}
        for f in child:
            name = _local(f.tag)
            if name in ("loc", "lastmod", "changefreq", "priority"):
                e[name] = (f.text or "").strip()
        entries.append(e)
    return {"type": "index" if kind == "sitemapindex" else "urlset", "entries": entries}


def validate(parsed, source_url="", raw_size=0):
    issues, entries = [], parsed["entries"]
    add = lambda *a, **k: issues.append(issue(*a, **k))
    if not entries:
        add("error", "EMPTY", "Sitemap has no entries.")
    if parsed["type"] == "urlset" and len(entries) > MAX_URLS:
        add("error", "TOO_MANY_URLS", "%d URLs; limit is %d per sitemap file." % (len(entries), MAX_URLS))
    if raw_size > MAX_BYTES:
        add("error", "TOO_LARGE", "Uncompressed size %d MB exceeds 50 MB." % (raw_size // 1048576))
    seen, dup = set(), []
    hosts = {}
    bad_loc, bad_mod, http_urls, long_urls, params = [], [], [], [], 0
    for e in entries:
        loc = e.get("loc", "")
        if not loc or not re.match(r"^https?://", loc):
            bad_loc.append(loc)
            continue
        if loc in seen:
            dup.append(loc)
        seen.add(loc)
        hosts[host_of(loc)] = hosts.get(host_of(loc), 0) + 1
        if loc.startswith("http://"):
            http_urls.append(loc)
        if len(loc) > 2048:
            long_urls.append(loc)
        if "?" in loc:
            params += 1
        lm = e.get("lastmod")
        if lm and not _LASTMOD.match(lm):
            bad_mod.append(lm)
        if e.get("priority"):
            try:
                if not 0 <= float(e["priority"]) <= 1:
                    raise ValueError
            except ValueError:
                add("warning", "BAD_PRIORITY", "priority %r outside 0.0-1.0" % e["priority"])
                break
    if bad_loc:
        add("error", "BAD_LOC", "%d entries have a missing or non-absolute <loc>." % len(bad_loc), examples=bad_loc[:3])
    if dup:
        add("warning", "DUPLICATES", "%d duplicate URLs." % len(dup), examples=dup[:3])
    if bad_mod:
        add("warning", "BAD_LASTMOD", "%d invalid <lastmod> values (use W3C datetime)." % len(bad_mod), examples=bad_mod[:3])
    if http_urls:
        add("warning", "HTTP_URLS", "%d http:// URLs listed; list canonical https URLs." % len(http_urls), examples=http_urls[:3])
    if long_urls:
        add("error", "URL_TOO_LONG", "%d URLs exceed 2048 chars." % len(long_urls))
    if len(hosts) > 1:
        add("warning", "MULTIPLE_HOSTS", "URLs from %d hosts: %s. A sitemap should cover one host." % (len(hosts), ", ".join(sorted(hosts))))
    if source_url and hosts and host_of(source_url) not in hosts and parsed["type"] == "urlset":
        add("warning", "HOST_MISMATCH", "Sitemap is on %s but lists URLs on %s." % (host_of(source_url), ", ".join(sorted(hosts))))
    if params > len(entries) * 0.2 and entries:
        add("info", "PARAM_URLS", "%d URLs contain query strings; sitemaps should list canonical URLs only." % params)
    with_mod = sum(1 for e in entries if e.get("lastmod"))
    if parsed["type"] == "urlset" and entries and with_mod == 0:
        add("info", "NO_LASTMOD", "No <lastmod> values. Accurate lastmod helps crawlers prioritise changed pages.")
    return sort_issues(issues)


def load(target, timeout=20.0, ua=None):
    from ..core import DEFAULT_UA
    if os.path.isfile(target):
        with open(target, "rb") as f:
            raw = f.read()
        return raw, "", 200
    resp = fetch(target, timeout=timeout, ua=ua or DEFAULT_UA, max_bytes=60 * 1024 * 1024)
    if resp.status == 0:
        raise ToolError("could not fetch %s: %s" % (target, resp.error))
    if not resp.ok:
        raise ToolError("%s returned HTTP %d" % (target, resp.status))
    return resp.body, resp.final_url, resp.status


def collect_urls(target, recurse=True, max_children=50, timeout=20.0, ua=None, _depth=0):
    """Return (urls, notes) following sitemap indexes."""
    raw, src, _ = load(target, timeout, ua)
    parsed = parse_sitemap(raw)
    if parsed["type"] != "index":
        return [e["loc"] for e in parsed["entries"] if e.get("loc")], []
    urls, notes = [], []
    if not recurse or _depth >= 2:
        return urls, ["index not expanded"]
    for e in parsed["entries"][:max_children]:
        try:
            u, n = collect_urls(e["loc"], recurse, max_children, timeout, ua, _depth + 1)
            urls += u
            notes += n
        except ToolError as ex:
            notes.append("%s: %s" % (e.get("loc"), ex))
    return urls, notes


def _args(p):
    p.add_argument("target", help="sitemap URL (xml/xml.gz/txt) or local file")
    p.add_argument("--recurse", action="store_true", help="for sitemap indexes, also parse the child sitemaps")
    p.add_argument("--check", type=int, default=0, metavar="N", help="HTTP-check up to N listed URLs (status, redirects)")
    p.add_argument("--workers", type=int, default=8)
    add_net_args(p)


def _run(a):
    raw, src, _ = load(a.target, a.timeout, a.ua)
    parsed = parse_sitemap(raw)
    size = len(gzip.decompress(raw)) if raw[:2] == b"\x1f\x8b" else len(raw)
    issues = validate(parsed, src or a.target, size)
    res = {"source": src or a.target, "type": parsed["type"], "entries": len(parsed["entries"]), "uncompressed_bytes": size}
    entries = parsed["entries"]
    if parsed["type"] == "index":
        res["children"] = [{"sitemap": e.get("loc"), "lastmod": e.get("lastmod")} for e in entries[:50]]
        if a.recurse:
            urls, notes = collect_urls(a.target, True, 50, a.timeout, a.ua)
            res["total_urls_in_children"] = len(urls)
            for n in notes:
                issues.append(issue("warning", "CHILD_ERROR", n))
            entries = [{"loc": u} for u in urls]
    else:
        res["sample"] = entries[:10]
    if a.check:
        sample = [e["loc"] for e in entries[:a.check] if e.get("loc")]
        results = check_urls(sample, a.workers, a.timeout, a.ua, follow=False)
        rows, bad = [], 0
        for u, r in results.items():
            rows.append({"url": u, "status": r.status, "redirect_to": r.headers.get("location", "") if 300 <= r.status < 400 else ""})
            if r.status != 200:
                bad += 1
        if bad:
            issues.append(issue("warning", "NON_200_URLS", "%d of %d checked URLs did not return 200; sitemaps should only list indexable 200 URLs." % (bad, len(sample))))
        res["checked"] = [r for r in rows if r["status"] != 200][:100] or "all %d checked URLs returned 200" % len(rows)
    res["issues"] = sort_issues(issues)
    return res


# ---------------------------------------------------------------- generator
def build_sitemap(urls, lastmod=None, changefreq=None, priority=None):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="%s">' % NS]
    for u in urls:
        out.append("  <url>")
        out.append("    <loc>%s</loc>" % escape(u))
        if lastmod:
            out.append("    <lastmod>%s</lastmod>" % lastmod)
        if changefreq:
            out.append("    <changefreq>%s</changefreq>" % changefreq)
        if priority is not None:
            out.append("    <priority>%s</priority>" % priority)
        out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def build_index(sitemap_urls, lastmod=None):
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<sitemapindex xmlns="%s">' % NS]
    for u in sitemap_urls:
        out.append("  <sitemap><loc>%s</loc>%s</sitemap>" % (escape(u), "<lastmod>%s</lastmod>" % lastmod if lastmod else ""))
    out.append("</sitemapindex>")
    return "\n".join(out) + "\n"


def _gen_args(p):
    p.add_argument("--urls", metavar="FILE", help="text file, one URL per line")
    p.add_argument("--crawl", metavar="URL", help="crawl this site and list indexable 200 pages")
    p.add_argument("--max-pages", type=int, default=500)
    p.add_argument("--lastmod", help="'today' or YYYY-MM-DD applied to every URL")
    p.add_argument("--changefreq", help="optional; Google ignores it")
    p.add_argument("--priority", help="optional; Google ignores it")
    p.add_argument("--split-dir", metavar="DIR", help="write sitemap-N.xml files + sitemap-index.xml here when over 50,000 URLs")
    p.add_argument("--base-url", help="public base URL for the index file (needed with --split-dir)")
    add_net_args(p)


def _gen_run(a):
    if not a.urls and not a.crawl:
        raise ToolError("give --urls FILE or --crawl URL")
    if a.urls:
        urls = read_lines(a.urls)
    else:
        from .crawler import Crawler
        c = Crawler(a.crawl, max_pages=a.max_pages, ua=a.ua, timeout=a.timeout)
        c.crawl()
        urls = [r["url"] for r in c.results if r["status"] == 200 and not r.get("noindex") and
                (not r.get("canonical") or r["canonical"] == r["url"])]
    seen, uniq = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    for u in uniq:
        if not re.match(r"^https?://", u):
            raise ToolError("not an absolute URL: %s" % u)
    lm = datetime.date.today().isoformat() if a.lastmod == "today" else a.lastmod
    if len(uniq) <= MAX_URLS or not a.split_dir:
        if len(uniq) > MAX_URLS:
            raise ToolError("%d URLs exceeds %d; pass --split-dir DIR --base-url URL" % (len(uniq), MAX_URLS))
        xml = build_sitemap(uniq, lm, a.changefreq, a.priority)
        return {"_raw": xml, "urls": len(uniq)}
    if not a.base_url:
        raise ToolError("--base-url is required with --split-dir")
    os.makedirs(a.split_dir, exist_ok=True)
    files = []
    for i in range(0, len(uniq), MAX_URLS):
        name = "sitemap-%d.xml" % (i // MAX_URLS + 1)
        with open(os.path.join(a.split_dir, name), "w", encoding="utf-8") as f:
            f.write(build_sitemap(uniq[i:i + MAX_URLS], lm, a.changefreq, a.priority))
        files.append(name)
    with open(os.path.join(a.split_dir, "sitemap-index.xml"), "w", encoding="utf-8") as f:
        f.write(build_index([a.base_url.rstrip("/") + "/" + n for n in files], lm))
    return {"urls": len(uniq), "files": files + ["sitemap-index.xml"], "dir": a.split_dir}


TOOLS = [
    Tool("sitemap", "SEO / Technical", "Parse + validate XML/text sitemaps (limits, dupes, lastmod); optionally HTTP-check URLs", _args, _run),
    Tool("sitemap-gen", "SEO / Technical", "Generate sitemap.xml from a URL list or a crawl (auto-splits at 50k URLs)", _gen_args, _gen_run),
]
