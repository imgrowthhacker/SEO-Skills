"""Server log analyzer: search-engine and AI-bot crawl behaviour from Apache/Nginx access logs."""
from __future__ import annotations

import datetime
import gzip
import re
import socket
import urllib.parse
from collections import Counter, defaultdict

from ..core import Tool, ToolError, issue, normalize_url, read_lines, sort_issues

LINE = re.compile(r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" '
                  r'(?P<status>\d{3}) (?P<bytes>\S+)(?: "(?P<ref>[^"]*)" "(?P<ua>[^"]*)")?')
# (substring, name, type) - first match wins, so specific tokens come first
BOTS = [
    ("adsbot-google", "AdsBot-Google", "ads"), ("googlebot-image", "Googlebot-Image", "search"),
    ("googlebot-video", "Googlebot-Video", "search"), ("googlebot-news", "Googlebot-News", "search"),
    ("google-inspectiontool", "Google-InspectionTool", "search"), ("storebot-google", "Storebot-Google", "search"),
    ("googleother", "GoogleOther", "search"), ("google-extended", "Google-Extended", "ai"), ("googlebot", "Googlebot", "search"),
    ("bingbot", "Bingbot", "search"), ("bingpreview", "BingPreview", "search"), ("slurp", "Yahoo Slurp", "search"),
    ("duckduckbot", "DuckDuckBot", "search"), ("baiduspider", "Baiduspider", "search"), ("yandexbot", "YandexBot", "search"),
    ("applebot", "Applebot", "search"),
    ("gptbot", "GPTBot", "ai"), ("oai-searchbot", "OAI-SearchBot", "ai"), ("chatgpt-user", "ChatGPT-User", "ai"),
    ("claudebot", "ClaudeBot", "ai"), ("claude-web", "Claude-Web", "ai"), ("anthropic-ai", "anthropic-ai", "ai"),
    ("perplexitybot", "PerplexityBot", "ai"), ("ccbot", "CCBot", "ai"), ("bytespider", "Bytespider", "ai"),
    ("amazonbot", "Amazonbot", "ai"), ("meta-externalagent", "meta-externalagent", "ai"),
    ("ahrefsbot", "AhrefsBot", "seo-tool"), ("semrushbot", "SemrushBot", "seo-tool"), ("mj12bot", "MJ12bot", "seo-tool"),
    ("dotbot", "DotBot", "seo-tool"), ("screaming frog", "Screaming Frog", "seo-tool"),
    ("facebookexternalhit", "Facebook", "social"), ("twitterbot", "Twitterbot", "social"), ("linkedinbot", "LinkedInBot", "social"),
]
STATIC = re.compile(r"\.(css|js|mjs|png|jpe?g|gif|webp|avif|svg|ico|woff2?|ttf|eot|map|mp4|webm)(\?|$)", re.I)
VERIFY = {"Googlebot": (".googlebot.com", ".google.com"), "Bingbot": (".search.msn.com",)}


def identify(ua):
    low = (ua or "").lower()
    for sub, name, typ in BOTS:
        if sub in low:
            return name, typ
    return None, None


def parse_line(line):
    m = LINE.match(line)
    if not m:
        return None
    d = m.groupdict()
    try:
        ts = datetime.datetime.strptime(d["time"], "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        ts = None
    d["ts"] = ts
    d["status"] = int(d["status"])
    d["bytes"] = int(d["bytes"]) if d["bytes"].isdigit() else 0
    d["ua"] = d.get("ua") or ""
    return d


def verify_ip(ip, bot):
    suffixes = VERIFY.get(bot)
    if not suffixes:
        return None
    try:
        host = socket.gethostbyaddr(ip)[0]
        if not host.endswith(suffixes):
            return False
        return ip in {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except (socket.herror, socket.gaierror, OSError):
        return False


def analyze_lines(lines, focus="Googlebot", top=20, important=None, verify_dns=0):
    total = parsed = 0
    per_bot = defaultdict(lambda: {"type": "", "hits": 0, "urls": set(), "bytes": 0, "s2": 0, "s3": 0, "s4": 0, "s5": 0, "static": 0, "params": 0})
    focus_urls, focus_status_urls = Counter(), defaultdict(Counter)
    daily = defaultdict(Counter)
    ips = defaultdict(Counter)
    first = last = None
    focus_paths = set()
    for line in lines:
        total += 1
        d = parse_line(line)
        if not d:
            continue
        parsed += 1
        name, typ = identify(d["ua"])
        if not name:
            continue
        b = per_bot[name]
        b["type"] = typ
        b["hits"] += 1
        b["urls"].add(d["path"])
        b["bytes"] += d["bytes"]
        b["s%d" % (d["status"] // 100)] = b.get("s%d" % (d["status"] // 100), 0) + 1
        if STATIC.search(d["path"]):
            b["static"] += 1
        if "?" in d["path"]:
            b["params"] += 1
        if d["ts"]:
            daily[name][d["ts"].date().isoformat()] += 1
            first = d["ts"] if not first or d["ts"] < first else first
            last = d["ts"] if not last or d["ts"] > last else last
        if name in VERIFY:
            ips[name][d["ip"]] += 1
        if name == focus:
            focus_urls[d["path"]] += 1
            focus_status_urls[d["status"]][d["path"]] += 1
            focus_paths.add(urllib.parse.urlsplit(d["path"]).path or "/")
    if parsed == 0:
        raise ToolError("no lines matched the Apache/Nginx combined log format (checked %d lines)" % total)
    bots = []
    for n, b in sorted(per_bot.items(), key=lambda kv: -kv[1]["hits"]):
        bots.append({"bot": n, "type": b["type"], "hits": b["hits"], "unique_urls": len(b["urls"]),
                     "2xx": b["s2"], "3xx": b["s3"], "4xx": b["s4"], "5xx": b["s5"],
                     "static_pct": round(100.0 * b["static"] / b["hits"], 1), "param_url_pct": round(100.0 * b["params"] / b["hits"], 1),
                     "mb": round(b["bytes"] / 1048576.0, 2)})
    issues = []
    res = {"lines": total, "parsed": parsed, "period": "%s to %s" % (first.date(), last.date()) if first and last else None,
           "bots": bots}
    f = per_bot.get(focus)
    if f:
        res["focus_bot"] = focus
        res["focus_top_urls"] = [{"url": u, "hits": h} for u, h in focus_urls.most_common(top)]
        res["focus_error_urls_4xx"] = [{"url": u, "hits": h} for u, h in focus_status_urls[404].most_common(top)] if 404 in focus_status_urls else []
        res["focus_redirected_urls"] = [{"url": u, "hits": h} for st in (301, 302, 307, 308) for u, h in focus_status_urls.get(st, Counter()).most_common(10)][:top]
        res["focus_server_errors"] = [{"url": u, "hits": h} for st in (500, 502, 503, 504) for u, h in focus_status_urls.get(st, Counter()).most_common(10)][:top]
        res["focus_daily_hits"] = [{"date": k, "hits": v} for k, v in sorted(daily[focus].items())][-31:]
        pct = lambda x: 100.0 * x / f["hits"]
        if pct(f["s4"]) > 10:
            issues.append(issue("warning", "CRAWL_WASTE_4XX", "%.0f%% of %s hits return 4xx." % (pct(f["s4"]), focus)))
        if pct(f["s3"]) > 15:
            issues.append(issue("warning", "CRAWL_WASTE_REDIRECTS", "%.0f%% of %s hits hit redirects; link straight to final URLs." % (pct(f["s3"]), focus)))
        if f["s5"]:
            issues.append(issue("error" if pct(f["s5"]) > 2 else "warning", "SERVER_ERRORS", "%s received %d 5xx responses." % (focus, f["s5"])))
        if pct(f["params"]) > 25:
            issues.append(issue("warning", "CRAWL_WASTE_PARAMS", "%.0f%% of %s hits are parameter URLs; consider canonicals/robots rules." % (pct(f["params"]), focus)))
        if important:
            gaps = [u for u in important if (urllib.parse.urlsplit(u).path or "/") not in focus_paths]
            res["important_urls_never_crawled"] = gaps
            if gaps:
                issues.append(issue("warning", "CRAWL_GAPS", "%d of %d important URLs were not requested by %s in this log window." % (len(gaps), len(important), focus)))
    else:
        issues.append(issue("info", "FOCUS_BOT_ABSENT", "No hits from %s in these logs." % focus))
    if verify_dns:
        fake = []
        for bot, counter in ips.items():
            for ip, hits in counter.most_common(verify_dns):
                ok = verify_ip(ip, bot)
                if ok is False:
                    fake.append({"claimed_bot": bot, "ip": ip, "hits": hits})
        res["unverified_bot_ips"] = fake or "all checked IPs passed reverse+forward DNS verification"
        if fake:
            issues.append(issue("warning", "FAKE_BOTS", "%d IPs claim to be Googlebot/Bingbot but fail DNS verification." % len(fake)))
    res["issues"] = sort_issues(issues)
    return res


def _open(path):
    try:
        if path.endswith(".gz"):
            return gzip.open(path, "rt", encoding="utf-8", errors="replace")
        return open(path, "r", encoding="utf-8", errors="replace")
    except OSError as e:
        raise ToolError("cannot read %s: %s" % (path, e.strerror or e))


def _args(p):
    p.add_argument("files", nargs="+", help="access log files (.log or .gz), combined log format")
    p.add_argument("--bot", default="Googlebot", help="bot to analyse in depth (default Googlebot)")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--urls", metavar="FILE", help="file of important URLs/paths: report which the bot never crawled")
    p.add_argument("--verify-dns", type=int, default=0, metavar="N", help="reverse-DNS verify up to N IPs per Google/Bing bot (needs network)")


def _run(a):
    def gen():
        for f in a.files:
            with _open(f) as fh:
                for line in fh:
                    yield line
    imp = [urllib.parse.urlsplit(u).path or "/" for u in read_lines(a.urls)] if a.urls else None
    return analyze_lines(gen(), a.bot, a.top, imp, a.verify_dns)


TOOLS = [Tool("logs", "SEO / Data & reporting", "Server log analysis: bot hits, status codes, crawl waste, crawl gaps, fake-bot check", _args, _run)]
