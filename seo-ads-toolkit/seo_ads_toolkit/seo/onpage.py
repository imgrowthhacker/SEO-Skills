"""On-page SEO analyzer for a single URL or local HTML file."""
from __future__ import annotations

import re
import urllib.parse

from ..core import (Tool, add_net_args, host_of, issue, load_page, normalize_url,
                    sort_issues, tokenize)
from .serp import text_width_px

GENERIC_ANCHORS = {"click here", "here", "read more", "more", "learn more", "link", "this", "click", "details"}


def _count(issues, sev):
    return sum(1 for i in issues if i["severity"] == sev)


def analyze_page(page, url="", keyword=None, status=None, headers=None):
    """Return a dict of findings for a parsed Page."""
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    url = url or page.url
    title = page.title
    # ---- title
    if not title:
        add("error", "TITLE_MISSING", "Page has no <title>.")
    else:
        if len(title) < 30:
            add("warning", "TITLE_SHORT", "Title is %d chars; aim for roughly 30-60." % len(title))
        if text_width_px(title, 20) > 600:
            add("warning", "TITLE_LONG", "Title is ~%d px / %d chars and will likely be truncated." % (text_width_px(title, 20), len(title)))
        if page.title_count > 1:
            add("warning", "TITLE_MULTIPLE", "%d <title> tags found." % page.title_count)
    # ---- description
    descs = page.meta.get("description", [])
    d = page.description
    if not descs or not d:
        add("warning", "DESC_MISSING", "No meta description.")
    else:
        if len(d) < 70:
            add("info", "DESC_SHORT", "Meta description is %d chars; 70-160 is typical." % len(d))
        if len(d) > 160:
            add("warning", "DESC_LONG", "Meta description is %d chars and may be truncated." % len(d))
        if len(descs) > 1:
            add("warning", "DESC_MULTIPLE", "%d meta descriptions found." % len(descs))
    # ---- headings
    h1s = page.h1s
    if not h1s:
        add("error", "H1_MISSING", "No <h1>.")
    else:
        if len(h1s) > 1:
            add("warning", "H1_MULTIPLE", "%d <h1> tags; one clear primary heading is the safer pattern." % len(h1s))
        if any(not h for h in h1s):
            add("warning", "H1_EMPTY", "An <h1> is empty.")
    prev = 0
    for lvl, _t in page.headings:
        if prev and lvl > prev + 1:
            add("info", "HEADING_SKIP", "Heading level jumps from h%d to h%d." % (prev, lvl))
            break
        prev = lvl
    # ---- indexability / canonical
    if page.noindex:
        add("error", "NOINDEX", "Meta robots contains noindex: this page will not be indexed.")
    xrt = (headers or {}).get("x-robots-tag", "")
    if "noindex" in xrt.lower():
        add("error", "NOINDEX_HEADER", "X-Robots-Tag header contains noindex.")
    if not page.canonical:
        add("warning", "CANONICAL_MISSING", "No rel=canonical.")
    else:
        if len(set(page.canonical)) > 1:
            add("error", "CANONICAL_MULTIPLE", "Conflicting canonical URLs: %s" % ", ".join(sorted(set(page.canonical))))
        c = page.canonical[0]
        if url and normalize_url(c) != normalize_url(url):
            if host_of(c) != host_of(url):
                add("info", "CANONICAL_OFFSITE", "Canonical points to another host: %s" % c)
            else:
                add("info", "CANONICAL_OTHER", "Canonical points to a different URL: %s" % c)
    # ---- technical basics
    if not page.viewport:
        add("warning", "VIEWPORT_MISSING", "No meta viewport (hurts mobile rendering).")
    if not page.lang:
        add("info", "LANG_MISSING", "<html> has no lang attribute.")
    if not page.charset and "charset" not in (headers or {}).get("content-type", "").lower():
        add("info", "CHARSET_MISSING", "No charset declared.")
    # ---- images
    imgs = page.images
    no_alt = [i for i in imgs if i["alt"] is None]
    if no_alt:
        add("warning", "IMG_ALT_MISSING", "%d of %d images have no alt attribute." % (len(no_alt), len(imgs)),
            examples=[i["src"] for i in no_alt[:3]])
    nodim = [i for i in imgs if not (i["width"] and i["height"])]
    if nodim:
        add("info", "IMG_NO_DIMENSIONS", "%d images lack width/height (can cause layout shift)." % len(nodim))
    # ---- content
    wc = page.word_count
    if wc < 300:
        add("warning" if wc < 100 else "info", "THIN_CONTENT", "Main content has only %d words." % wc)
    # ---- social / structured data
    if not page.og.get("title") or not page.og.get("image"):
        add("info", "OG_INCOMPLETE", "Open Graph title/image missing (affects link previews).")
    if not page.twitter.get("card") and not page.og:
        add("info", "TWITTER_CARD_MISSING", "No twitter:card or Open Graph tags.")
    if not page.jsonld and not page.microdata_types:
        add("info", "STRUCTURED_DATA_NONE", "No JSON-LD or microdata found.")
    # ---- links
    internal = external = nofollow = empty = generic = 0
    for l in page.links:
        if not l.url:
            continue
        same = (host_of(l.url) == host_of(url)) if url else True
        internal += 1 if same else 0
        external += 0 if same else 1
        nofollow += 1 if l.nofollow else 0
        if not l.text:
            empty += 1
        elif l.text.lower() in GENERIC_ANCHORS:
            generic += 1
    if page.links and internal == 0:
        add("warning", "NO_INTERNAL_LINKS", "No internal links found on the page.")
    if len(page.links) > 300:
        add("info", "MANY_LINKS", "%d links on one page." % len(page.links))
    if generic:
        add("info", "GENERIC_ANCHORS", "%d links use generic anchor text like 'click here'." % generic)
    # ---- mixed content / URL hygiene
    if url.startswith("https://"):
        mixed = [i["src"] for i in imgs if i["src"].startswith("http://")]
        mixed += [s["src"] for s in page.scripts if s["src"].startswith("http://")]
        mixed += [s for s in page.stylesheets if s.startswith("http://")]
        if mixed:
            add("warning", "MIXED_CONTENT", "%d http:// resources on an https page." % len(mixed), examples=mixed[:3])
    elif url.startswith("http://"):
        add("warning", "NOT_HTTPS", "Page is served over http.")
    if url:
        path = urllib.parse.urlsplit(url).path
        if len(url) > 115:
            add("info", "URL_LONG", "URL is %d chars." % len(url))
        if re.search(r"[A-Z]", path):
            add("info", "URL_UPPERCASE", "URL path contains uppercase letters.")
        if "_" in path:
            add("info", "URL_UNDERSCORE", "URL path uses underscores; hyphens are the convention.")
    if page.refresh:
        add("warning", "META_REFRESH", "Meta refresh redirect present: %s" % page.refresh)
    # ---- keyword placement
    kw_report = None
    if keyword:
        k = keyword.lower()
        body_tokens = tokenize(page.main_text)
        first100 = " ".join(body_tokens[:100])
        kw_report = {
            "keyword": keyword,
            "in_title": k in title.lower(), "in_h1": any(k in h.lower() for h in h1s),
            "in_description": k in d.lower(), "in_url": k.replace(" ", "-") in url.lower() or k.replace(" ", "") in url.lower(),
            "in_first_100_words": k in first100,
            "occurrences": len(re.findall(re.escape(k), page.main_text.lower())),
        }
        kw_report["density_pct"] = round(100.0 * kw_report["occurrences"] * len(k.split()) / max(1, len(body_tokens)), 2)
        for key, label in (("in_title", "title"), ("in_h1", "H1"), ("in_description", "meta description")):
            if not kw_report[key]:
                add("warning" if key != "in_description" else "info", "KEYWORD_NOT_IN_" + label.upper().replace(" ", "_"),
                    "Target keyword '%s' not found in %s." % (keyword, label))
    issues = sort_issues(issues)
    score = max(0, 100 - 15 * _count(issues, "error") - 5 * _count(issues, "warning") - _count(issues, "info"))
    res = {
        "url": url, "status": status, "score": score,
        "title": title, "title_chars": len(title), "description": d, "description_chars": len(d),
        "h1": h1s, "canonical": page.canonical[0] if page.canonical else None,
        "robots_meta": page.robots_meta or None, "lang": page.lang or None,
        "word_count": wc, "images": len(imgs), "internal_links": internal, "external_links": external,
        "nofollow_links": nofollow, "empty_anchor_links": empty,
        "structured_data": len(page.jsonld) + len(page.microdata_types),
        "outline": [{"level": "h%d" % l, "text": t[:90]} for l, t in page.headings[:40]],
        "issues": issues,
    }
    if kw_report:
        res["keyword"] = kw_report
    return res


def _args(p):
    p.add_argument("target", help="URL or local .html file")
    p.add_argument("--keyword", "-k", help="target keyword to check placement for")
    add_net_args(p)


def _run(a):
    page, resp = load_page(a.target, a.ua, a.timeout)
    url = resp.final_url if resp else ""
    return analyze_page(page, url, a.keyword, resp.status if resp else None, resp.headers if resp else None)


TOOLS = [Tool("onpage", "SEO / On-page", "Audit one page: title, meta, headings, images, links, canonical, keyword placement", _args, _run)]
