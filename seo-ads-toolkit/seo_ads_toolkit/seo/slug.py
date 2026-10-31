"""URL slug generator and URL quality checker."""
from __future__ import annotations

import re
import unicodedata
import urllib.parse

from ..core import STOPWORDS, Tool, ToolError, issue, sort_issues

_TRANSLIT = {"ß": "ss", "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "œ": "oe", "Œ": "OE", "đ": "d", "ł": "l", "Ł": "L", "þ": "th"}


def slugify(text, max_len=60, drop_stopwords=False, sep="-"):
    t = "".join(_TRANSLIT.get(c, c) for c in text)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", t)
    if drop_stopwords:
        kept = [w for w in words if w not in STOPWORDS]
        words = kept or words
    slug = sep.join(words)
    if len(slug) > max_len:
        cut = slug[:max_len]
        if sep in cut and slug[max_len:max_len + 1] != sep:
            cut = cut.rsplit(sep, 1)[0]
        slug = cut.strip(sep)
    return slug


def check_url(url):
    p = urllib.parse.urlsplit(url)
    path = p.path
    issues = []
    add = lambda *a: issues.append(issue(*a))
    if p.scheme == "http":
        add("warning", "NOT_HTTPS", "URL uses http.")
    if len(url) > 115:
        add("warning", "TOO_LONG", "URL is %d chars; keep it short and readable." % len(url))
    if re.search(r"[A-Z]", path):
        add("warning", "UPPERCASE", "Uppercase in path can create duplicate URLs.")
    if "_" in path:
        add("info", "UNDERSCORES", "Use hyphens instead of underscores to separate words.")
    if " " in url or "%20" in path:
        add("warning", "SPACES", "Spaces in URL.")
    if re.search(r"[^\x00-\x7f]", path):
        add("info", "NON_ASCII", "Non-ASCII characters (they will be percent-encoded).")
    if p.query:
        n = len(urllib.parse.parse_qsl(p.query))
        add("info", "PARAMETERS", "%d query parameter(s); make sure parameter URLs are canonicalised or blocked." % n)
        if re.search(r"(sessionid|sid|phpsessid|jsessionid)=", p.query, re.I):
            add("error", "SESSION_ID", "Session ID in URL creates endless duplicate URLs.")
    if "//" in path:
        add("warning", "DOUBLE_SLASH", "Double slash in path.")
    depth = len([s for s in path.split("/") if s])
    if depth > 4:
        add("info", "DEEP_PATH", "Path is %d levels deep." % depth)
    if re.search(r"\.(php|aspx?|jsp|html?)$", path, re.I):
        add("info", "FILE_EXTENSION", "File extension in URL; extensionless URLs are easier to maintain.")
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if last and re.fullmatch(r"\d+", last):
        add("info", "NUMERIC_SLUG", "Slug is only digits; descriptive words help users and relevance.")
    stops = [w for w in last.split("-") if w in STOPWORDS]
    if len(stops) >= 3:
        add("info", "STOPWORDS", "Slug has %d stop words (%s)." % (len(stops), ", ".join(stops)))
    return {"url": url, "length": len(url), "depth": depth, "issues": sort_issues(issues)}


def _args(p):
    p.add_argument("items", nargs="+", help="text to slugify, or URLs to check (auto-detected)")
    p.add_argument("--max-len", type=int, default=60)
    p.add_argument("--drop-stopwords", action="store_true")
    p.add_argument("--sep", default="-")


def _run(a):
    out = []
    for it in a.items:
        if re.match(r"^https?://", it, re.I):
            out.append(check_url(it))
        else:
            out.append({"input": it, "slug": slugify(it, a.max_len, a.drop_stopwords, a.sep)})
    if not out:
        raise ToolError("nothing to do")
    if len(out) == 1:
        return out[0]
    return {"results": out}


TOOLS = [Tool("slug", "SEO / On-page", "Generate clean URL slugs, or check URL quality (length, case, params...)", _args, _run)]
