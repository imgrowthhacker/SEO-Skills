"""hreflang validator, with optional reciprocal-link verification."""
from __future__ import annotations

import re

from ..core import Tool, add_net_args, fetch, issue, load_page, normalize_url, parse_html, sort_issues

ISO639_1 = set("""aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee
el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka
kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no
nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th
ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu""".split())
_CODE = re.compile(r"^([a-z]{2,3})(-[A-Za-z]{4})?(-([A-Za-z]{2}|\d{3}))?$", re.I)
WRONG_REGION = {"uk": "GB", "eu": "(none; EU is not a valid region — use a language, or x-default)", "la": "(LA is not a country)"}


def validate_codes(pairs):
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    seen = {}
    for code, href in pairs:
        c = code.strip()
        if c.lower() == "x-default":
            seen.setdefault("x-default", []).append(href)
            continue
        if "_" in c:
            add("error", "UNDERSCORE", "%r uses an underscore; use a hyphen (%s)." % (c, c.replace("_", "-")))
            continue
        m = _CODE.match(c)
        if not m:
            add("error", "BAD_CODE", "%r is not a valid language[-region] code." % c)
            continue
        lang = m.group(1).lower()
        if len(lang) == 2 and lang not in ISO639_1:
            add("error", "BAD_LANGUAGE", "%r: %r is not an ISO 639-1 language code." % (c, lang))
        region = m.group(4)
        if region and region.lower() in WRONG_REGION:
            add("error", "BAD_REGION", "%r: region %r is wrong; use %s." % (c, region, WRONG_REGION[region.lower()]))
        seen.setdefault(c.lower(), []).append(href)
    for c, hrefs in seen.items():
        if len(set(hrefs)) > 1:
            add("error", "CONFLICT", "%s points to several URLs: %s" % (c, ", ".join(sorted(set(hrefs)))))
    for _c, href in pairs:
        if not re.match(r"^https?://", href):
            add("error", "NOT_ABSOLUTE", "hreflang URL must be absolute: %s" % href)
            break
    return issues


def analyze(page, url, verify=False, timeout=15.0, ua=None):
    from ..core import DEFAULT_UA
    pairs = page.hreflang
    issues = []
    if not pairs:
        return {"url": url, "annotations": [], "issues": [issue("info", "NONE", "No hreflang annotations in <head>.")]}
    issues += validate_codes(pairs)
    me = normalize_url(url) if url else ""
    urls = [normalize_url(h) for _c, h in pairs]
    if me and me not in urls:
        issues.append(issue("error", "NO_SELF_REFERENCE", "This page is not listed in its own hreflang set."))
    if not any(c.lower() == "x-default" for c, _ in pairs):
        issues.append(issue("info", "NO_X_DEFAULT", "No x-default fallback annotation."))
    if page.canonical and me and normalize_url(page.canonical[0]) != me:
        issues.append(issue("warning", "CANONICAL_MISMATCH", "Canonical points elsewhere; hreflang should be on canonical URLs."))
    rows = [{"hreflang": c, "url": h} for c, h in pairs]
    if verify:
        for row in rows:
            n = normalize_url(row["url"])
            if me and n == me:
                row["status"], row["returns_to_us"] = "self", "-"
                continue
            r = fetch(row["url"], timeout=timeout, ua=ua or DEFAULT_UA)
            row["status"] = r.status or r.error[:30]
            if r.status != 200:
                issues.append(issue("error", "ALT_NOT_200", "%s returns %s (alternates must be 200 and indexable)." % (row["url"], row["status"])))
                row["returns_to_us"] = "-"
                continue
            if normalize_url(r.final_url) != n:
                issues.append(issue("warning", "ALT_REDIRECTS", "%s redirects to %s" % (row["url"], r.final_url)))
            back = parse_html(r.text, r.final_url).hreflang
            ok = any(normalize_url(h) == me for _c, h in back)
            row["returns_to_us"] = ok
            if not ok and me:
                issues.append(issue("error", "NO_RETURN_LINK", "%s does not link back to %s (hreflang must be reciprocal)." % (row["url"], url)))
    return {"url": url, "annotations": rows, "issues": sort_issues(issues)}


def _args(p):
    p.add_argument("target", help="URL or local HTML file")
    p.add_argument("--verify", action="store_true", help="fetch each alternate and check status + return links")
    add_net_args(p)


def _run(a):
    page, resp = load_page(a.target, a.ua, a.timeout)
    return analyze(page, resp.final_url if resp else "", a.verify, a.timeout, a.ua)


TOOLS = [Tool("hreflang", "SEO / Technical", "Validate hreflang tags: codes, self-reference, x-default, reciprocity", _args, _run)]
