"""Shared plumbing for every tool: HTTP, HTML parsing, text helpers, output.

Standard library only, so the toolkit runs anywhere Python 3.8+ runs.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

VERSION = "1.0.0"
DEFAULT_UA = "SEOAdsToolkit/%s (open-source SEO and Ads tools)" % VERSION

Tool = namedtuple("Tool", "name category help add_args run")


class ToolError(Exception):
    """A user-facing error: bad input, unreachable site, missing file."""


# --------------------------------------------------------------------------
# Issues
# --------------------------------------------------------------------------
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def issue(severity, code, message, **extra):
    d = {"severity": severity, "code": code, "message": message}
    d.update(extra)
    return d


def sort_issues(issues):
    return sorted(issues, key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Response(object):
    def __init__(self, url, final_url, status=0, headers=None, body=b"",
                 elapsed=0.0, ttfb=0.0, chain=None, error=""):
        self.url = url
        self.final_url = final_url
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.elapsed = elapsed
        self.ttfb = ttfb
        self.chain = chain or []
        self.error = error

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def content_type(self):
        return self.headers.get("content-type", "").split(";")[0].strip().lower()

    @property
    def is_html(self):
        ct = self.content_type
        if ct in ("text/html", "application/xhtml+xml"):
            return True
        return not ct and b"<html" in self.body[:2048].lower()

    @property
    def text(self):
        return decode_body(self.body, self.headers.get("content-type", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ensure_scheme(url):
    url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def _hdrs(msg):
    out = {}
    for k, v in msg.items():
        k = k.lower()
        out[k] = out[k] + ", " + v if k in out else v
    return out


def _decompress(body, enc, limit):
    try:
        if enc in ("gzip", "x-gzip"):
            return zlib.decompressobj(zlib.MAX_WBITS | 16).decompress(body, limit)
        try:
            return zlib.decompressobj().decompress(body, limit)
        except zlib.error:
            return zlib.decompressobj(-zlib.MAX_WBITS).decompress(body, limit)
    except Exception:
        return body


def fetch(url, method="GET", timeout=15.0, ua=DEFAULT_UA, follow=True,
          max_redirects=10, headers=None, max_bytes=5 * 1024 * 1024, data=None):
    """Fetch a URL. Never raises: failures are reported in Response.error."""
    url = ensure_scheme(url)
    hdr = {"User-Agent": ua, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"}
    if headers:
        hdr.update(headers)
    opener = urllib.request.build_opener(_NoRedirect)
    chain, seen = [], set()
    cur, status, rheaders, body, error, ttfb = url, 0, {}, b"", "", 0.0
    t0 = time.time()
    for _ in range(max_redirects + 1):
        if cur in seen:
            error = "redirect loop"
            break
        seen.add(cur)
        if not cur.lower().startswith(("http://", "https://")):
            error = "unsupported URL scheme"
            chain.append((cur, 0))
            break
        req = urllib.request.Request(cur, data=data, headers=hdr, method=method)
        t1 = time.time()
        try:
            resp = opener.open(req, timeout=timeout)
            ttfb = time.time() - t1
            status, rheaders = resp.status, _hdrs(resp.headers)
            body = b"" if method == "HEAD" else resp.read(max_bytes + 1)
            resp.close()
        except urllib.error.HTTPError as e:
            ttfb = time.time() - t1
            status, rheaders = e.code, _hdrs(e.headers)
            try:
                body = b"" if method == "HEAD" else e.read(max_bytes + 1)
            except Exception:
                body = b""
        except Exception as e:
            error = "%s: %s" % (type(e).__name__, getattr(e, "reason", None) or e)
            status = 0
            chain.append((cur, 0))
            break
        chain.append((cur, status))
        loc = rheaders.get("location")
        if follow and status in (301, 302, 303, 307, 308) and loc:
            cur = urllib.parse.urljoin(cur, loc)
            if method not in ("GET", "HEAD") and status in (301, 302, 303):
                method, data = "GET", None
            continue
        break
    else:
        error = "too many redirects"
    body = body[:max_bytes]
    enc = rheaders.get("content-encoding", "").lower()
    if body and enc in ("gzip", "x-gzip", "deflate"):
        body = _decompress(body, enc, max_bytes * 4)
    return Response(url, cur, status, rheaders, body, time.time() - t0, ttfb, chain, error)


def check_urls(urls, workers=8, timeout=10.0, ua=DEFAULT_UA, follow=True):
    """HEAD-check many URLs (falling back to GET). Returns {url: Response}."""
    def one(u):
        r = fetch(u, method="HEAD", timeout=timeout, ua=ua, follow=follow)
        if r.error or r.status in (403, 405, 501):
            r2 = fetch(u, method="GET", timeout=timeout, ua=ua, follow=follow, max_bytes=65536)
            if not r2.error or r.error:
                r = r2
        return u, r
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return dict(ex.map(one, list(urls)))


def decode_body(raw, content_type=""):
    import codecs
    m = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    cands = []
    if m:
        cands.append(m.group(1))
    head = raw[:4096].decode("ascii", "ignore")
    m2 = re.search(r"<meta[^>]+charset=[\"']?([\w.-]+)", head, re.I)
    if m2:
        cands.append(m2.group(1))
    cands.append("utf-8")
    for c in cands:
        try:
            codecs.lookup(c)
            return raw.decode(c, "replace")
        except (LookupError, ValueError):
            continue
    return raw.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------
def normalize_url(url):
    """Lowercase scheme/host, drop fragment and default port, give '/' path."""
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return url
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    port = p.port if p.netloc and ":" in p.netloc.rsplit("@", 1)[-1] and p.port else None
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = "%s:%d" % (host, port)
    path = p.path or "/"
    return urllib.parse.urlunsplit((scheme, host, path, p.query, ""))


def host_of(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def strip_www(host):
    return host[4:] if host.startswith("www.") else host


def same_site(a, b, subdomains=False):
    ha, hb = strip_www(host_of(a)), strip_www(host_of(b))
    if ha == hb:
        return True
    return bool(subdomains and (ha.endswith("." + hb) or hb.endswith("." + ha)))


def origin_of(url):
    p = urllib.parse.urlsplit(url)
    return "%s://%s" % (p.scheme, p.netloc)


# --------------------------------------------------------------------------
# HTML parsing
# --------------------------------------------------------------------------
Link = namedtuple("Link", "href url text rel nofollow")


class Page(object):
    def __init__(self, url=""):
        self.url = url
        self.title = ""
        self.title_count = 0
        self.meta = {}
        self.og = {}
        self.twitter = {}
        self.canonical = []
        self.lang = ""
        self.charset = ""
        self.headings = []
        self.links = []
        self.images = []
        self.scripts = []
        self.stylesheets = []
        self.link_tags = []
        self.hreflang = []
        self.jsonld = []
        self.iframes = []
        self.microdata_types = []
        self.forms = 0
        self.form_fields = 0
        self.buttons = []
        self.tel_links = []
        self.refresh = ""
        self.text = ""
        self.main_text = ""
        self.html_length = 0

    @property
    def description(self):
        v = self.meta.get("description", [""])
        return v[0].strip() if v else ""

    @property
    def viewport(self):
        v = self.meta.get("viewport", [""])
        return v[0] if v else ""

    @property
    def robots_meta(self):
        parts = []
        for k in ("robots", "googlebot", "bingbot"):
            parts.extend(self.meta.get(k, []))
        return ",".join(parts).lower()

    @property
    def noindex(self):
        return "noindex" in self.robots_meta or "none" in [x.strip() for x in self.robots_meta.split(",")]

    @property
    def h1s(self):
        return [t for l, t in self.headings if l == 1]

    @property
    def word_count(self):
        return len(tokenize(self.main_text))


_SKIP_TEXT = {"script", "style", "noscript", "template", "title"}
_BOILER = {"nav", "footer", "aside"}


class _Parser(HTMLParser):
    def __init__(self, page, base):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.p = page
        self.base = base
        self.skip = 0
        self.boiler = 0
        self.in_head = False
        self.in_title = False
        self.title_parts = []
        self.heading = None
        self.anchor = None
        self.ld = None
        self.text_parts = []
        self.main_parts = []

    def _abs(self, href):
        href = (href or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "data:", "#", "sms:")):
            return ""
        try:
            return urllib.parse.urljoin(self.base, href) if self.base else href
        except ValueError:
            return ""

    def _close_anchor(self):
        a = self.anchor
        if a:
            text = re.sub(r"\s+", " ", "".join(a["parts"])).strip()
            rel = a["rel"].lower()
            self.p.links.append(Link(a["href"], a["url"], text, rel, "nofollow" in rel.split()))
            self.anchor = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        p = self.p
        if "itemtype" in a and a["itemtype"]:
            p.microdata_types.append(a["itemtype"])
        if tag == "html":
            p.lang = a.get("lang", "") or a.get("xml:lang", "")
        elif tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head = False
        elif tag == "base" and a.get("href"):
            self.base = urllib.parse.urljoin(self.base, a["href"]) if self.base else a["href"]
        elif tag == "title":
            self.in_title = True
            p.title_count += 1
        elif tag == "meta":
            name = a.get("name", "").lower()
            prop = a.get("property", "").lower()
            content = a.get("content", "")
            if a.get("charset"):
                p.charset = a["charset"]
            he = a.get("http-equiv", "").lower()
            if he == "content-type":
                m = re.search(r"charset=([\w-]+)", content, re.I)
                if m:
                    p.charset = m.group(1)
            if he == "refresh":
                p.refresh = content
            if prop.startswith("og:"):
                p.og.setdefault(prop[3:], content)
            elif name.startswith("twitter:") or prop.startswith("twitter:"):
                p.twitter.setdefault((name or prop)[8:], content)
            if name:
                p.meta.setdefault(name, []).append(content)
        elif tag == "link":
            rels = a.get("rel", "").lower().split()
            href = self._abs(a.get("href"))
            p.link_tags.append({"rel": " ".join(rels), "href": href, "as": a.get("as", ""),
                                "type": a.get("type", ""), "hreflang": a.get("hreflang", "")})
            if "canonical" in rels and href:
                p.canonical.append(href)
            elif "alternate" in rels and a.get("hreflang") and href:
                p.hreflang.append((a["hreflang"], href))
            elif "stylesheet" in rels and href:
                p.stylesheets.append(href)
        elif tag == "a":
            self._close_anchor()
            self.anchor = {"href": a.get("href", ""), "url": self._abs(a.get("href")),
                           "rel": a.get("rel", ""), "parts": []}
            if a.get("href", "").lower().startswith("tel:"):
                p.tel_links.append(a["href"])
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading = (int(tag[1]), [])
        elif tag == "img":
            p.images.append({"src": self._abs(a.get("src", a.get("data-src", ""))),
                             "alt": a.get("alt"), "width": a.get("width"), "height": a.get("height"),
                             "loading": a.get("loading", ""), "srcset": bool(a.get("srcset"))})
        elif tag == "script":
            typ = a.get("type", "").lower()
            if typ == "application/ld+json":
                self.ld = []
            else:
                p.scripts.append({"src": self._abs(a.get("src")) if a.get("src") else "",
                                  "async": "async" in a, "defer": "defer" in a,
                                  "module": typ == "module", "in_head": self.in_head})
        elif tag == "iframe":
            p.iframes.append(self._abs(a.get("src")))
        elif tag == "form":
            p.forms += 1
        elif tag in ("input", "select", "textarea"):
            if tag != "input" or a.get("type", "text").lower() not in ("hidden", "submit", "button", "image", "reset"):
                p.form_fields += 1
            if tag == "input" and a.get("type", "").lower() in ("submit", "button") and a.get("value"):
                p.buttons.append(a["value"].strip())
        elif tag == "button":
            self._btn = []
        if tag in _SKIP_TEXT:
            self.skip += 1
        if tag in _BOILER:
            self.boiler += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ("script", "style", "title", "a", "button"):
            self.handle_endtag(tag)
        else:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        p = self.p
        if tag == "head":
            self.in_head = False
        elif tag == "title":
            self.in_title = False
            if not p.title:
                p.title = re.sub(r"\s+", " ", "".join(self.title_parts)).strip()
        elif tag == "a":
            self._close_anchor()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self.heading:
                p.headings.append((self.heading[0], re.sub(r"\s+", " ", "".join(self.heading[1])).strip()))
                self.heading = None
        elif tag == "script" and self.ld is not None:
            p.jsonld.append("".join(self.ld))
            self.ld = None
        if tag == "button" and getattr(self, "_btn", None) is not None:
            t = re.sub(r"\s+", " ", "".join(self._btn)).strip()
            if t:
                p.buttons.append(t)
            self._btn = None
        if tag in _SKIP_TEXT and self.skip > 0:
            self.skip -= 1
        if tag in _BOILER and self.boiler > 0:
            self.boiler -= 1

    def handle_data(self, data):
        if self.ld is not None:
            self.ld.append(data)
            return
        if self.in_title:
            self.title_parts.append(data)
        if self.skip:
            return
        if self.heading:
            self.heading[1].append(data)
        if self.anchor:
            self.anchor["parts"].append(data)
        if getattr(self, "_btn", None) is not None:
            self._btn.append(data)
        self.text_parts.append(data)
        if not self.boiler:
            self.main_parts.append(data)


def parse_html(html, url=""):
    page = Page(url)
    page.html_length = len(html)
    parser = _Parser(page, url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    parser._close_anchor()
    page.text = re.sub(r"\s+", " ", " ".join(parser.text_parts)).strip()
    page.main_text = re.sub(r"\s+", " ", " ".join(parser.main_parts)).strip()
    return page


def load_page(target, ua=DEFAULT_UA, timeout=15.0):
    """target = URL or path to a local HTML file. Returns (Page, Response|None)."""
    if os.path.isfile(target):
        with open(target, "rb") as f:
            raw = f.read()
        return parse_html(decode_body(raw), ""), None
    resp = fetch(target, ua=ua, timeout=timeout)
    if resp.status == 0:
        raise ToolError("could not fetch %s: %s" % (target, resp.error or "no response"))
    return parse_html(resp.text, resp.final_url), resp


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
STOPWORDS = frozenset("""a about above after again against all also am an and any are aren't as at be because been
before being below between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down
during each few for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself just let's me more most
mustn't my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd
she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's these
they they'd they'll they're they've this those through to too under until up us very was wasn't we we'd we'll we're
we've were weren't what what's when when's where where's which while who who's whom why why's will with won't would
wouldn't you you'd you'll you're you've your yours yourself yourselves""".split())

_WORD = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*", re.UNICODE)


def tokenize(text):
    return [w.lower().replace("\u2019", "'") for w in _WORD.findall(text or "")]


def ngrams(tokens, n):
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def stem(w):
    """Very light English stemmer: good enough for grouping, not linguistics."""
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("sses"):
        return w[:-2]
    if w.endswith(("ss", "us", "is")):
        return w
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) > len(suf) + 3:
            b = w[:-len(suf)]
            if len(b) > 2 and b[-1] == b[-2] and b[-1] not in "aeiouls":
                b = b[:-1]
            return b
    if w.endswith("es") and len(w) > 4 and (w[-3] in "sxz" or w.endswith(("ches", "shes"))):
        return w[:-2]
    if w.endswith("s") and len(w) > 3:
        return w[:-1]
    return w


# --------------------------------------------------------------------------
# Tables / numbers / files
# --------------------------------------------------------------------------
def to_number(s):
    """Parse '1,234.5', '12.3%', '$4.50', '--', '< 10%' into float (or None)."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    if t in ("", "--", "-", "n/a", "N/A", "null", "None"):
        return None
    t = re.sub(r"[^\d.,\-+eE]", "", t.replace("\u2212", "-"))
    if not t or t in ("-", "+", ".", ","):
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", t):
            t = t.replace(",", "")
        else:
            t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def read_text_file(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise ToolError("cannot read %s: %s" % (path, e.strerror or e))
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def read_table(path_or_text, is_text=False):
    """Read CSV/TSV (any common encoding) into a list of row lists."""
    text = path_or_text if is_text else read_text_file(path_or_text)
    sample = "\n".join(text.splitlines()[:20])
    delim = ","
    counts = {d: sample.count(d) for d in (",", "\t", ";")}
    if max(counts.values()) > 0:
        delim = max(counts, key=counts.get)
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def find_col(header, *needles, **kw):
    """Index of first header cell containing any needle (case-insens), else None.
    kw: exclude=(...) substrings that disqualify a cell."""
    exclude = kw.get("exclude", ())
    for i, h in enumerate(header):
        hl = h.strip().lower()
        if any(n in hl for n in needles) and not any(x in hl for x in exclude):
            return i
    return None


def read_lines(path):
    return [l.strip() for l in read_text_file(path).splitlines() if l.strip() and not l.strip().startswith("#")]


def parse_ratio(v):
    """'30%', '0.3' or '30' -> 0.3"""
    if v is None:
        return None
    s = str(v).strip()
    pct = s.endswith("%")
    n = to_number(s)
    if n is None:
        raise ToolError("not a number: %r" % v)
    if pct or n > 1:
        n = n / 100.0
    return n


def safe_div(a, b, nd=4):
    if a is None or b in (None, 0):
        return None
    return round(a / b, nd)


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------
def add_net_args(p):
    p.add_argument("--timeout", type=float, default=15.0, help="request timeout in seconds (default 15)")
    p.add_argument("--ua", default=DEFAULT_UA, help="User-Agent to send")


def _scalar(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return ("%.4f" % v).rstrip("0").rstrip(".") if abs(v) < 1000 else "%.1f" % v
    return str(v)


def _is_flat_rows(obj):
    return bool(obj) and all(isinstance(x, dict) and x and not any(isinstance(y, (dict, list)) for y in x.values()) for x in obj)


def _table(rows, pad, max_rows):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    cell = lambda v: _scalar(v).replace("\n", " ")
    widths = {c: min(60, max(len(c), *[len(cell(r.get(c))) for r in rows[:max_rows]])) for c in cols}
    fmt = lambda vals: pad + "  ".join(v[:widths[c]].ljust(widths[c]) for c, v in zip(cols, vals)).rstrip()
    out = [fmt(cols), pad + "  ".join("-" * widths[c] for c in cols)]
    for r in rows[:max_rows]:
        out.append(fmt([cell(r.get(c)) for c in cols]))
    if len(rows) > max_rows:
        out.append("%s... %d more rows (use --json or --out for everything)" % (pad, len(rows) - max_rows))
    return out


def render_lines(obj, indent=0, max_rows=40):
    pad = "  " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):
                continue
            if isinstance(v, (dict, list)):
                if not v:
                    lines.append("%s%s: (none)" % (pad, k))
                else:
                    lines.append("%s%s:" % (pad, k))
                    lines.extend(render_lines(v, indent + 1, max_rows))
            else:
                lines.append("%s%s: %s" % (pad, k, _scalar(v)))
    elif isinstance(obj, list):
        if _is_flat_rows(obj):
            lines.extend(_table(obj, pad, max_rows))
        else:
            for x in obj[:max_rows]:
                if isinstance(x, (dict, list)):
                    lines.append(pad + "-")
                    lines.extend(render_lines(x, indent + 1, max_rows))
                else:
                    lines.append("%s- %s" % (pad, _scalar(x)))
            if len(obj) > max_rows:
                lines.append("%s... %d more" % (pad, len(obj) - max_rows))
    else:
        lines.append(pad + _scalar(obj))
    return lines


def render_text(obj):
    return "\n".join(render_lines(obj))


def first_table(result):
    best = []
    if isinstance(result, dict):
        for v in result.values():
            if isinstance(v, list) and _is_flat_rows(v) and len(v) > len(best):
                best = v
    return best


def write_csv(path, rows, columns=None):
    cols = columns or []
    if not cols:
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([("" if r.get(c) is None else r.get(c)) for c in cols])


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
