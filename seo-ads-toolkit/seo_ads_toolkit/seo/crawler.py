"""Polite multi-threaded site crawler + single-page link checker."""
from __future__ import annotations

import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from ..core import (DEFAULT_UA, Tool, ToolError, add_net_args, check_urls, ensure_scheme, fetch,
                    host_of, normalize_url, origin_of, parse_html, same_site, write_csv)
from .robots import Robots

SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|pdf|docx?|xlsx?|pptx?|zip|gz|rar|7z|tar|mp[34]|mov|avi|wmv|"
                      r"woff2?|ttf|eot|otf|css|js|json|xml|rss|atom|exe|dmg|apk|csv|txt)(\?.*)?$", re.I)


class Crawler(object):
    def __init__(self, start, max_pages=100, max_depth=5, workers=4, delay=0.0, ua=DEFAULT_UA,
                 timeout=15.0, respect_robots=True, include=None, exclude=None, subdomains=False,
                 keep_pages=True):
        self.start = normalize_url(ensure_scheme(start))
        self.max_pages, self.max_depth, self.workers = max_pages, max_depth, max(1, workers)
        self.delay, self.ua, self.timeout = delay, ua, timeout
        self.respect_robots, self.subdomains, self.keep_pages = respect_robots, subdomains, keep_pages
        self.include = re.compile(include) if include else None
        self.exclude = re.compile(exclude) if exclude else None
        self.results, self.pages, self.edges = [], {}, {}
        self.blocked, self.external, self.sitemaps = [], {}, []
        self.robots, self.truncated = None, False
        self.depth = {self.start: 0}

    def _allowed_url(self, url):
        if not url.startswith(("http://", "https://")):
            return False
        if not same_site(url, self.start, self.subdomains):
            return False
        if SKIP_EXT.search(urllib.parse.urlsplit(url).path):
            return False
        if self.include and not self.include.search(url):
            return False
        if self.exclude and self.exclude.search(url):
            return False
        return True

    def _fetch_one(self, url):
        if self.delay:
            time.sleep(self.delay)
        return url, fetch(url, timeout=self.timeout, ua=self.ua, follow=False)

    def crawl(self):
        rob = fetch(origin_of(self.start) + "/robots.txt", timeout=self.timeout, ua=self.ua)
        self.robots = Robots(rob.text if rob.ok else "")
        self.sitemaps = list(self.robots.sitemaps)
        crawl_delay = self.robots.crawl_delay(self.ua) if self.respect_robots else None
        if crawl_delay and crawl_delay > self.delay:
            self.delay = min(crawl_delay, 10)
        seen, frontier = {self.start}, [self.start]
        while frontier:
            room = self.max_pages - len(self.results)
            if room <= 0:
                self.truncated = True
                break
            batch = frontier[:room]
            if len(frontier) > room:
                self.truncated = True
            nxt = []
            todo = []
            for u in batch:
                if self.respect_robots and not self.robots.is_allowed(self.ua, u):
                    self.blocked.append(u)
                else:
                    todo.append(u)
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                fetched = list(ex.map(self._fetch_one, todo))
            for url, resp in fetched:
                rec = self._record(url, resp)
                self.results.append(rec)
                d = self.depth[url]
                targets = []
                if rec["redirect_to"]:
                    targets.append((rec["redirect_to"], "", False))
                page = self.pages.get(url)
                if page is not None:
                    for l in page.links:
                        if l.url:
                            targets.append((normalize_url(l.url), l.text, l.nofollow))
                    self.edges[url] = [(t, txt, nf) for t, txt, nf in targets]
                for t, _txt, nf in targets:
                    if not t.startswith(("http://", "https://")):
                        continue
                    if not same_site(t, self.start, self.subdomains):
                        self.external.setdefault(t, url)
                        continue
                    if t in seen or not self._allowed_url(t) or d + 1 > self.max_depth:
                        continue
                    seen.add(t)
                    self.depth[t] = d + 1
                    nxt.append(t)
            frontier = nxt
        self._finish()
        return self

    def _record(self, url, resp):
        rec = {"url": url, "status": resp.status, "depth": self.depth.get(url, 0),
               "content_type": resp.content_type, "title": "", "description": "", "h1": "", "h1_count": 0,
               "word_count": 0, "size_bytes": len(resp.body), "load_ms": int(resp.elapsed * 1000),
               "canonical": "", "noindex": False, "redirect_to": "", "inlinks": 0, "error": resp.error}
        if 300 <= resp.status < 400 and resp.headers.get("location"):
            rec["redirect_to"] = normalize_url(urllib.parse.urljoin(url, resp.headers["location"]))
        elif resp.status and resp.is_html and resp.body:
            page = parse_html(resp.text, resp.final_url)
            rec.update({"title": page.title, "description": page.description, "h1": (page.h1s or [""])[0],
                        "h1_count": len(page.h1s), "word_count": page.word_count,
                        "canonical": normalize_url(page.canonical[0]) if page.canonical else "",
                        "noindex": page.noindex or "noindex" in resp.headers.get("x-robots-tag", "").lower()})
            if self.keep_pages:
                self.pages[url] = page
                page._headers = resp.headers
        return rec

    def _finish(self):
        counts = {}
        for src, edges in self.edges.items():
            for t, _txt, _nf in set((t, "", nf) for t, _txt, nf in edges):
                if t != src:
                    counts[t] = counts.get(t, 0) + 1
        for r in self.results:
            r["inlinks"] = counts.get(r["url"], 0)
        by_url = {r["url"]: r for r in self.results}
        self.broken = []
        for src, edges in self.edges.items():
            for t, txt, _nf in edges:
                r = by_url.get(t)
                if r and (r["status"] >= 400 or r["status"] == 0):
                    self.broken.append({"source": src, "target": t, "status": r["status"] or r["error"], "anchor": txt[:60]})

    def summary(self):
        res = self.results
        by_status = {}
        for r in res:
            by_status[str(r["status"] or "error")] = by_status.get(str(r["status"] or "error"), 0) + 1
        html = [r for r in res if r["content_type"] in ("text/html", "application/xhtml+xml")]

        def dupes(key):
            groups = {}
            for r in html:
                if r[key] and r["status"] == 200:
                    groups.setdefault(r[key].strip().lower(), []).append(r["url"])
            return [{"value": k[:80], "pages": len(v), "examples": " | ".join(v[:3])} for k, v in groups.items() if len(v) > 1]

        return {
            "start": self.start, "pages_crawled": len(res), "truncated": self.truncated,
            "blocked_by_robots": len(self.blocked), "status_counts": by_status,
            "broken_links": self.broken[:200],
            "redirects": [{"url": r["url"], "status": r["status"], "to": r["redirect_to"]} for r in res if r["redirect_to"]][:100],
            "noindex_pages": [r["url"] for r in res if r["noindex"]][:50],
            "missing_title": [r["url"] for r in html if r["status"] == 200 and not r["title"]][:50],
            "missing_description": [r["url"] for r in html if r["status"] == 200 and not r["description"]][:50],
            "missing_h1": [r["url"] for r in html if r["status"] == 200 and r["h1_count"] == 0][:50],
            "duplicate_titles": dupes("title"), "duplicate_descriptions": dupes("description"), "duplicate_h1": dupes("h1"),
            "deep_pages_gt3_clicks": [{"url": r["url"], "depth": r["depth"]} for r in res if r["depth"] > 3][:50],
            "slowest_pages": [{"url": r["url"], "load_ms": r["load_ms"]} for r in sorted(res, key=lambda x: -x["load_ms"])[:10]],
            "largest_pages": [{"url": r["url"], "kb": r["size_bytes"] // 1024} for r in sorted(res, key=lambda x: -x["size_bytes"])[:10]],
            "thin_pages_lt200_words": [{"url": r["url"], "words": r["word_count"]} for r in html if r["status"] == 200 and r["word_count"] < 200][:50],
            "external_links_found": len(self.external),
        }


def _args(p):
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--delay", type=float, default=0.0, help="seconds to wait before each request")
    p.add_argument("--include", help="only crawl URLs matching this regex")
    p.add_argument("--exclude", help="skip URLs matching this regex")
    p.add_argument("--ignore-robots", action="store_true", help="do not obey robots.txt (only for sites you own)")
    p.add_argument("--subdomains", action="store_true")
    p.add_argument("--check-external", type=int, default=0, metavar="N", help="HTTP-check up to N external links")
    p.add_argument("--csv", metavar="FILE", help="write every crawled page to CSV")
    add_net_args(p)


def _run(a):
    c = Crawler(a.url, a.max_pages, a.max_depth, a.workers, a.delay, a.ua, a.timeout,
                not a.ignore_robots, a.include, a.exclude, a.subdomains)
    c.crawl()
    s = c.summary()
    if a.check_external:
        ext = list(c.external)[:a.check_external]
        out = check_urls(ext, a.workers, a.timeout, a.ua)
        s["broken_external"] = [{"url": u, "status": r.status or r.error, "found_on": c.external[u]} for u, r in out.items() if r.status == 0 or r.status >= 400]
    if a.csv:
        write_csv(a.csv, c.results)
        s["csv"] = a.csv
    s["pages"] = [{k: r[k] for k in ("url", "status", "depth", "title", "word_count", "load_ms", "inlinks")} for r in c.results]
    return s


# ---------------------------------------------------------------- link checker
def _links_args(p):
    p.add_argument("url")
    p.add_argument("--internal-only", action="store_true")
    p.add_argument("--max", type=int, default=300, help="max unique links to check")
    p.add_argument("--workers", type=int, default=8)
    add_net_args(p)


def _links_run(a):
    resp = fetch(a.url, timeout=a.timeout, ua=a.ua)
    if resp.status == 0:
        raise ToolError("could not fetch %s: %s" % (a.url, resp.error))
    page = parse_html(resp.text, resp.final_url)
    first = {}
    for l in page.links:
        if l.url.startswith(("http://", "https://")):
            first.setdefault(normalize_url(l.url), l)
    urls = [u for u in first if not a.internal_only or same_site(u, resp.final_url)][:a.max]
    results = check_urls(urls, a.workers, a.timeout, a.ua, follow=False)
    rows = []
    for u in urls:
        r = results[u]
        kind = "ok"
        if r.status == 0:
            kind = "error"
        elif r.status >= 400:
            kind = "broken"
        elif 300 <= r.status < 400:
            kind = "redirect"
        rows.append({"url": u, "status": r.status or r.error[:40], "kind": kind,
                     "type": "internal" if same_site(u, resp.final_url) else "external",
                     "anchor": first[u].text[:40], "nofollow": first[u].nofollow,
                     "redirect_to": r.headers.get("location", "") if kind == "redirect" else ""})
    problems = [r for r in rows if r["kind"] != "ok"]
    issues = []
    from ..core import issue
    for r in problems:
        issues.append(issue("error" if r["kind"] in ("broken", "error") else "info", "LINK_" + r["kind"].upper(),
                            "%s -> %s" % (r["url"], r["status"])))
    return {"page": resp.final_url, "links_checked": len(rows), "problems": len(problems),
            "issues": issues, "problem_links": problems, "all_links": rows}


TOOLS = [
    Tool("crawl", "SEO / Technical", "Crawl a site: status codes, titles, duplicates, broken links, depth, redirects, speed", _args, _run),
    Tool("links", "SEO / Technical", "Check every link on one page for broken/redirecting targets", _links_args, _links_run),
]
