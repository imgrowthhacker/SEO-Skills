"""robots.txt parser, tester (Google-style longest-match rules) and generator."""
from __future__ import annotations

import re
import urllib.parse

from ..core import (Tool, ToolError, add_net_args, fetch, issue, origin_of, read_text_file,
                    sort_issues)

AI_CRAWLERS = ["GPTBot", "ChatGPT-User", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", "Claude-Web",
               "PerplexityBot", "CCBot", "Google-Extended", "Applebot-Extended", "Bytespider",
               "cohere-ai", "Amazonbot", "meta-externalagent", "Diffbot"]
KNOWN = {"user-agent", "allow", "disallow", "sitemap", "crawl-delay", "host", "clean-param", "noindex"}


class Robots(object):
    def __init__(self, text=""):
        self.groups = []
        self.sitemaps = []
        self.errors = []
        self.size = len(text.encode("utf-8", "ignore"))
        self._parse(text)

    def _parse(self, text):
        cur, last_was_ua = None, False
        for n, raw in enumerate(text.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                self.errors.append((n, "line has no ':' separator: %r" % raw.strip()[:60]))
                continue
            field, value = [s.strip() for s in line.split(":", 1)]
            f = field.lower()
            if f not in KNOWN:
                self.errors.append((n, "unknown directive %r" % field))
                continue
            if f == "user-agent":
                if cur is None or not last_was_ua:
                    cur = {"agents": [], "rules": [], "crawl_delay": None, "noindex": []}
                    self.groups.append(cur)
                cur["agents"].append(value.lower())
                last_was_ua = True
                continue
            last_was_ua = False
            if f == "sitemap":
                self.sitemaps.append(value)
            elif f in ("allow", "disallow"):
                if cur is None:
                    self.errors.append((n, "%s rule before any User-agent" % field))
                    continue
                cur["rules"].append((f, value))
            elif f == "crawl-delay":
                if cur is not None:
                    try:
                        cur["crawl_delay"] = float(value)
                    except ValueError:
                        self.errors.append((n, "invalid crawl-delay %r" % value))
            elif f == "noindex" and cur is not None:
                cur["noindex"].append(value)

    def _rules_for(self, ua):
        ua = ua.lower()
        best, matched = -1, []
        for g in self.groups:
            for tok in g["agents"]:
                if tok == "*":
                    spec = 0
                elif tok in ua:
                    spec = len(tok)
                else:
                    continue
                if spec > best:
                    best, matched = spec, [g]
                elif spec == best and g not in matched:
                    matched.append(g)
        rules = []
        for g in matched:
            rules.extend(g["rules"])
        delay = next((g["crawl_delay"] for g in matched if g["crawl_delay"] is not None), None)
        return rules, delay

    @staticmethod
    def _pattern(path):
        anchored = path.endswith("$")
        if anchored:
            path = path[:-1]
        rx = ".*".join(re.escape(p) for p in path.split("*"))
        return re.compile("^" + rx + ("$" if anchored else ""))

    def check(self, ua, url):
        """Return (allowed, matched_rule_text)."""
        p = urllib.parse.urlsplit(url)
        path = (p.path or "/") + ("?" + p.query if p.query else "")
        if not url.startswith(("http://", "https://")):
            path = url if url.startswith("/") else "/" + url
        rules, _ = self._rules_for(ua)
        best = None
        for kind, pat in rules:
            if pat == "":
                continue
            if self._pattern(pat).match(path):
                key = (len(pat), 1 if kind == "allow" else 0)
                if best is None or key > best[0]:
                    best = (key, kind, pat)
        if best is None:
            return True, None
        return best[1] == "allow", "%s: %s" % (best[1].capitalize(), best[2])

    def is_allowed(self, ua, url):
        return self.check(ua, url)[0]

    def crawl_delay(self, ua):
        return self._rules_for(ua)[1]


def analyze(text):
    r = Robots(text)
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    if not r.groups:
        add("warning", "NO_GROUPS", "No User-agent groups found.")
    star = [g for g in r.groups if "*" in g["agents"]]
    if r.groups and not star:
        add("info", "NO_STAR_GROUP", "No 'User-agent: *' group; unlisted bots are unrestricted.")
    for g in star:
        if ("disallow", "/") in g["rules"] and ("allow", "/") not in g["rules"]:
            add("error", "BLOCKS_ALL", "'User-agent: *' has 'Disallow: /' — the whole site is blocked for crawlers.")
    if not r.sitemaps:
        add("info", "NO_SITEMAP", "No Sitemap: directive. Declaring your sitemap helps discovery.")
    for s in r.sitemaps:
        if not s.startswith(("http://", "https://")):
            add("warning", "SITEMAP_NOT_ABSOLUTE", "Sitemap URL must be absolute: %s" % s)
    for g in r.groups:
        for kind, pat in g["rules"]:
            if kind == "disallow" and re.search(r"(\.js|\.css|/wp-includes/|/wp-content/(themes|plugins)/|/assets/|/static/)", pat):
                add("warning", "BLOCKS_RESOURCES", "Disallow %s may block CSS/JS Google needs to render pages." % pat)
        if g["crawl_delay"]:
            add("info", "CRAWL_DELAY", "Crawl-delay %s is ignored by Google (Bing honours it)." % g["crawl_delay"])
        if g["noindex"]:
            add("warning", "NOINDEX_DIRECTIVE", "'Noindex:' in robots.txt is not supported by Google; use meta robots or X-Robots-Tag.")
    if r.size > 500 * 1024:
        add("error", "TOO_LARGE", "robots.txt is %d KiB; Google ignores content past 500 KiB." % (r.size // 1024))
    for n, msg in r.errors:
        add("warning", "SYNTAX", "line %d: %s" % (n, msg))
    return r, sort_issues(issues)


def _args(p):
    p.add_argument("target", help="site URL, direct robots.txt URL, or local file")
    p.add_argument("--test", action="append", default=[], metavar="URL_OR_PATH", help="URL/path to test (repeatable)")
    p.add_argument("--ua", default="Googlebot", help="user-agent for --test (default Googlebot)")
    p.add_argument("--timeout", type=float, default=15.0)


def _run(a):
    import os
    status = None
    if os.path.isfile(a.target):
        text, src = read_text_file(a.target), a.target
    else:
        t = a.target if a.target.lower().endswith("robots.txt") else origin_of(re.sub(r"^(?!https?://)", "https://", a.target)) + "/robots.txt"
        resp = fetch(t, timeout=a.timeout)
        if resp.status == 0:
            raise ToolError("could not fetch %s: %s" % (t, resp.error))
        status, src = resp.status, resp.final_url
        text = resp.text if resp.ok else ""
    r, issues = analyze(text)
    if status is not None and status != 200:
        if status == 404 or status == 410:
            issues.insert(0, issue("info", "NO_ROBOTS", "robots.txt returned %d: crawlers treat that as 'everything allowed'." % status))
        elif status >= 500:
            issues.insert(0, issue("error", "ROBOTS_5XX", "robots.txt returned %d: Google may pause crawling the site." % status))
    res = {"source": src, "http_status": status, "size_bytes": r.size,
           "groups": [{"user_agents": ", ".join(g["agents"]), "allow": sum(1 for k, _ in g["rules"] if k == "allow"),
                       "disallow": sum(1 for k, v in g["rules"] if k == "disallow" and v), "crawl_delay": g["crawl_delay"]}
                      for g in r.groups],
           "sitemaps": r.sitemaps}
    if a.test:
        tests = []
        for u in a.test:
            ok, rule = r.check(a.ua, u)
            tests.append({"url": u, "user_agent": a.ua, "allowed": ok, "matched_rule": rule or "(no rule; default allow)"})
            if not ok:
                issues.append(issue("info", "TEST_BLOCKED", "%s is blocked for %s by %s" % (u, a.ua, rule)))
        res["tests"] = tests
    res["issues"] = sort_issues(issues)
    return res


# ---------------------------------------------------------------- generator
PRESETS = {
    "wordpress": ["/wp-admin/", "/wp-login.php", "/?s=", "/search/", "/*?replytocom="],
    "ecommerce": ["/cart", "/checkout", "/account", "/my-account", "/*?sort=", "/*?filter=", "/*?orderby="],
    "generic": ["/admin/", "/login", "/tmp/", "/*?sessionid="],
}


def generate(disallow=(), allow=(), sitemaps=(), block_ai=False, crawl_delay=None, ua="*", preset=None, allow_all=False):
    lines = ["User-agent: %s" % ua]
    paths = list(PRESETS.get(preset, [])) if preset else []
    paths += list(disallow)
    if allow_all and not paths:
        lines.append("Disallow:")
    for p in allow:
        lines.append("Allow: %s" % p)
    for p in paths:
        lines.append("Disallow: %s" % p)
    if preset == "wordpress" and "/wp-admin/admin-ajax.php" not in allow:
        lines.append("Allow: /wp-admin/admin-ajax.php")
    if crawl_delay:
        lines.append("Crawl-delay: %s" % crawl_delay)
    if block_ai:
        lines.append("")
        for bot in AI_CRAWLERS:
            lines += ["User-agent: %s" % bot, "Disallow: /", ""]
        lines.pop()
    if sitemaps:
        lines.append("")
        lines += ["Sitemap: %s" % s for s in sitemaps]
    return "\n".join(lines) + "\n"


def _gen_args(p):
    p.add_argument("--disallow", action="append", default=[], metavar="PATH")
    p.add_argument("--allow", action="append", default=[], metavar="PATH")
    p.add_argument("--sitemap", action="append", default=[], metavar="URL")
    p.add_argument("--preset", choices=sorted(PRESETS), help="starter rules for a platform")
    p.add_argument("--block-ai", action="store_true", help="add Disallow: / for known AI training/crawler user-agents")
    p.add_argument("--crawl-delay", type=float)
    p.add_argument("--ua", default="*")


def _gen_run(a):
    text = generate(a.disallow, a.allow, a.sitemap, a.block_ai, a.crawl_delay, a.ua, a.preset, allow_all=True)
    r, issues = analyze(text)
    return {"_raw": text, "robots_txt": text, "issues": issues}


TOOLS = [
    Tool("robots", "SEO / Technical", "Fetch + lint robots.txt and test URLs against it (Google-style matching)", _args, _run),
    Tool("robots-gen", "SEO / Technical", "Generate robots.txt (presets, sitemap lines, optional AI-crawler blocking)", _gen_args, _gen_run),
]
