"""One-command site audit: crawl + robots + sitemap + headers + redirects + on-page, with HTML report."""
from __future__ import annotations

import datetime
import html as _html

from ..core import (Tool, ToolError, add_net_args, fetch, issue, normalize_url, origin_of,
                    SEVERITY_ORDER, write_csv, write_text)
from .crawler import Crawler
from .headers import analyze_headers
from .onpage import analyze_page
from .redirects import variants
from .robots import analyze as analyze_robots
from .sitemap import collect_urls


def _agg(index, sev, code, msg, url, **extra):
    d = index.setdefault(code, {"severity": sev, "code": code, "message": msg, "pages": 0, "examples": []})
    d["pages"] += 1
    if len(d["examples"]) < 5 and url:
        d["examples"].append(url)
    return d


def run_audit(start, max_pages=100, max_depth=5, workers=4, delay=0.0, ua=None, timeout=15.0, respect_robots=True,
              check_variants=True):
    from ..core import DEFAULT_UA
    ua = ua or DEFAULT_UA
    c = Crawler(start, max_pages, max_depth, workers, delay, ua, timeout, respect_robots)
    c.crawl()
    if not c.results:
        raise ToolError("nothing was crawled: the start URL may be unreachable or blocked by robots.txt")
    idx, page_rows, scores = {}, [], []
    site_issues = []

    # ---- page level
    for rec in c.results:
        url = rec["url"]
        page = c.pages.get(url)
        if rec["status"] == 200 and page is not None:
            res = analyze_page(page, url, status=200, headers=getattr(page, "_headers", {}))
            scores.append(res["score"])
            for i in res["issues"]:
                _agg(idx, i["severity"], i["code"], i["message"], url)
            page_rows.append({"url": url, "score": res["score"], "title": res["title"][:70], "words": res["word_count"],
                              "errors": sum(1 for i in res["issues"] if i["severity"] == "error"),
                              "warnings": sum(1 for i in res["issues"] if i["severity"] == "warning"),
                              "depth": rec["depth"], "load_ms": rec["load_ms"], "inlinks": rec["inlinks"]})
        elif rec["status"] >= 400 or rec["status"] == 0:
            _agg(idx, "error", "HTTP_ERROR_PAGE", "Page returns an error status (4xx/5xx/unreachable).", "%s (%s)" % (url, rec["status"] or rec["error"]))
        elif rec["redirect_to"]:
            _agg(idx, "info", "REDIRECTING_URL", "Internal URL redirects (update links to point at the final URL).", "%s -> %s" % (url, rec["redirect_to"]))
        if rec["load_ms"] > 2000 and rec["status"] == 200:
            _agg(idx, "warning", "SLOW_RESPONSE", "Page took more than 2 s to download.", "%s (%d ms)" % (url, rec["load_ms"]))
        if rec["depth"] > 3 and rec["status"] == 200:
            _agg(idx, "info", "DEEP_PAGE", "Page is more than 3 clicks from the start page.", url)
    for b in c.broken:
        _agg(idx, "error", "BROKEN_INTERNAL_LINK", "Internal link points at a broken URL.", "%s -> %s (%s)" % (b["source"], b["target"], b["status"]))
    summ = c.summary()
    for key, code, msg in (("duplicate_titles", "DUPLICATE_TITLE", "Same <title> used on several pages."),
                           ("duplicate_descriptions", "DUPLICATE_DESCRIPTION", "Same meta description used on several pages."),
                           ("duplicate_h1", "DUPLICATE_H1", "Same <h1> used on several pages.")):
        for d in summ[key]:
            for _ in range(d["pages"]):
                _agg(idx, "warning", code, msg, d["examples"].split(" | ")[0])

    # ---- robots
    rob_text = fetch(origin_of(c.start) + "/robots.txt", timeout=timeout, ua=ua)
    robots_issues = []
    if rob_text.ok:
        _, robots_issues = analyze_robots(rob_text.text)
    elif rob_text.status in (404, 410):
        robots_issues = [issue("info", "NO_ROBOTS", "No robots.txt (crawlers assume everything is allowed).")]
    elif rob_text.status >= 500:
        robots_issues = [issue("error", "ROBOTS_5XX", "robots.txt returns %d." % rob_text.status)]
    for i in robots_issues:
        site_issues.append(dict(i, area="robots.txt"))

    # ---- sitemap
    sm_urls, sm_src = [], None
    cands = c.sitemaps or [origin_of(c.start) + "/sitemap.xml"]
    for s in cands[:3]:
        try:
            sm_urls, notes = collect_urls(s, True, 20, timeout, ua)
            sm_src = s
            break
        except ToolError:
            continue
    if sm_src is None:
        site_issues.append(dict(issue("warning", "NO_SITEMAP", "No reachable XML sitemap (checked robots.txt Sitemap: and /sitemap.xml)."), area="sitemap"))
    else:
        smset = {normalize_url(u) for u in sm_urls}
        crawled_ok = {r["url"] for r in c.results if r["status"] == 200 and not r["noindex"]}
        bad = [r["url"] for r in c.results if r["url"] in smset and (r["status"] != 200 or r["noindex"])]
        for u in bad[:50]:
            _agg(idx, "warning", "SITEMAP_NON_INDEXABLE", "URL is in the sitemap but is not a 200 indexable page.", u)
        if not c.truncated:
            for u in list(smset - {r["url"] for r in c.results})[:50]:
                _agg(idx, "info", "SITEMAP_ONLY", "In sitemap but not reachable by following links (possible orphan, or beyond crawl depth).", u)
            for u in list(crawled_ok - smset)[:50]:
                _agg(idx, "info", "NOT_IN_SITEMAP", "Indexable page found by crawling but missing from the sitemap.", u)
        site_issues.append(dict(issue("info", "SITEMAP_FOUND", "%s lists %d URLs." % (sm_src, len(sm_urls))), area="sitemap"))

    # ---- homepage headers + canonical versions
    try:
        h = analyze_headers(fetch(c.start, timeout=timeout, ua=ua))
        for i in h["issues"]:
            site_issues.append(dict(i, area="homepage headers"))
    except ToolError:
        pass
    if check_variants:
        try:
            v = variants(c.start, timeout, ua)
            for i in v["issues"]:
                if i["code"] != "SINGLE_VERSION":
                    site_issues.append(dict(i, area="http/https/www"))
        except Exception:
            pass
    if c.start.startswith("http://"):
        site_issues.append(dict(issue("error", "SITE_NOT_HTTPS", "Start URL is served over http."), area="https"))

    # ---- scoring
    avg = sum(scores) / len(scores) if scores else 0
    penalty = 0
    for i in site_issues:
        penalty += {"error": 5, "warning": 2, "info": 0}[i["severity"]]
    for code in ("BROKEN_INTERNAL_LINK", "HTTP_ERROR_PAGE"):
        if code in idx:
            penalty += min(10, idx[code]["pages"])
    score = max(0, int(round(avg - penalty)))
    page_issues = sorted(idx.values(), key=lambda d: (SEVERITY_ORDER[d["severity"]], -d["pages"]))
    for d in page_issues:
        d["share_pct"] = round(100.0 * d["pages"] / max(1, len(c.results)), 1)
    priorities = [d for d in page_issues if d["severity"] in ("error", "warning")][:10]
    return {
        "start": c.start, "date": datetime.date.today().isoformat(), "score": score,
        "pages_crawled": len(c.results), "crawl_truncated": c.truncated,
        "avg_page_score": round(avg, 1),
        "errors": sum(d["pages"] for d in page_issues if d["severity"] == "error") + sum(1 for i in site_issues if i["severity"] == "error"),
        "warnings": sum(d["pages"] for d in page_issues if d["severity"] == "warning") + sum(1 for i in site_issues if i["severity"] == "warning"),
        "top_priorities": [{"issue": d["code"], "severity": d["severity"], "pages": d["pages"], "what": d["message"]} for d in priorities],
        "site_level_issues": sorted(site_issues, key=lambda i: SEVERITY_ORDER[i["severity"]]),
        "page_level_issues": page_issues,
        "pages": sorted(page_rows, key=lambda r: r["score"]),
        "status_counts": summ["status_counts"],
        "method_note": "Score = average per-page score (100 - 15/error - 5/warning - 1/info) minus site-level penalties. It is a triage aid, not a ranking prediction.",
        "_crawler": c,
    }


def render_html(r):
    e = _html.escape
    sev_color = {"error": "#c62828", "warning": "#e65100", "info": "#1565c0"}
    rows = []
    for d in r["page_level_issues"]:
        rows.append("<tr><td><span class=b style='background:%s'>%s</span></td><td>%s</td><td>%d</td><td>%s</td><td><details><summary>examples</summary>%s</details></td></tr>" % (
            sev_color[d["severity"]], e(d["severity"]), e(d["code"]), d["pages"], e(d["message"]),
            "<br>".join(e(x) for x in d["examples"])))
    site = "".join("<tr><td><span class=b style='background:%s'>%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
        sev_color[i["severity"]], e(i["severity"]), e(i.get("area", "")), e(i["code"]), e(i["message"])) for i in r["site_level_issues"])
    pages = "".join("<tr><td>%s</td><td>%d</td><td>%s</td><td>%d</td><td>%d</td><td>%d</td></tr>" % (
        e(p["url"]), p["score"], e(p["title"]), p["errors"], p["warnings"], p["words"]) for p in r["pages"][:300])
    prio = "".join("<li><b>%s</b> (%s, %d pages) &mdash; %s</li>" % (e(p["issue"]), e(p["severity"]), p["pages"], e(p["what"])) for p in r["top_priorities"])
    return """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SEO audit - %(start)s</title><style>
body{font:15px/1.5 system-ui,Segoe UI,Arial,sans-serif;max-width:1100px;margin:30px auto;padding:0 16px;color:#1d2433}
h1{margin-bottom:0}h2{margin-top:36px;border-bottom:1px solid #dde;padding-bottom:6px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}.card{border:1px solid #dde;border-radius:10px;padding:12px 18px;min-width:130px}
.card b{display:block;font-size:26px}table{border-collapse:collapse;width:100%%;font-size:13px}
td,th{border-bottom:1px solid #eef;padding:6px 8px;text-align:left;vertical-align:top;word-break:break-word}th{background:#f5f7fb}
.b{color:#fff;border-radius:4px;padding:1px 7px;font-size:12px}small{color:#667}</style>
<h1>SEO audit</h1><small>%(start)s &middot; %(date)s &middot; %(n)d pages crawled%(trunc)s</small>
<div class=cards><div class=card><b>%(score)d</b>site score</div><div class=card><b>%(err)d</b>errors</div>
<div class=card><b>%(warn)d</b>warnings</div><div class=card><b>%(n)d</b>pages</div></div>
<h2>Fix first</h2><ol>%(prio)s</ol>
<h2>Site-level findings</h2><table><tr><th>Severity<th>Area<th>Code<th>Detail</tr>%(site)s</table>
<h2>Page-level findings</h2><table><tr><th>Severity<th>Code<th>Pages<th>What<th>Where</tr>%(rows)s</table>
<h2>Pages (worst first)</h2><table><tr><th>URL<th>Score<th>Title<th>Errors<th>Warnings<th>Words</tr>%(pages)s</table>
<p><small>%(note)s</small></p></html>""" % {
        "start": e(r["start"]), "date": r["date"], "n": r["pages_crawled"], "score": r["score"], "err": r["errors"],
        "warn": r["warnings"], "prio": prio or "<li>No errors or warnings found.</li>", "site": site, "rows": rows and "".join(rows),
        "pages": pages, "note": e(r["method_note"]), "trunc": " (crawl limit reached; raise --max-pages)" if r["crawl_truncated"] else ""}


def _args(p):
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--ignore-robots", action="store_true")
    p.add_argument("--no-variants", action="store_true", help="skip the http/https/www canonical version check")
    p.add_argument("--html", metavar="FILE", help="write a standalone HTML report")
    p.add_argument("--csv", metavar="FILE", help="write per-page results to CSV")
    add_net_args(p)


def _run(a):
    r = run_audit(a.url, a.max_pages, a.max_depth, a.workers, a.delay, a.ua, a.timeout, not a.ignore_robots, not a.no_variants)
    r.pop("_crawler")
    if a.html:
        write_text(a.html, render_html(r))
        r["html_report"] = a.html
    if a.csv:
        write_csv(a.csv, r["pages"])
        r["csv"] = a.csv
    r["issues"] = [dict(i, area=i.get("area")) for i in r["site_level_issues"]]
    return r


TOOLS = [Tool("audit", "SEO / On-page", "Full site audit in one command: crawl, robots, sitemap, headers, redirects, on-page + HTML report", _args, _run)]
