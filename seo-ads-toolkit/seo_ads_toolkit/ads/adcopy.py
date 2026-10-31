"""Ad copy validator for Google/Microsoft RSAs and common social ad formats."""
from __future__ import annotations

import json
import re
import unicodedata

from ..core import Tool, ToolError, issue, read_text_file, sort_issues, tokenize

# Limits as commonly published by each platform. They change; override with --max-* flags.
PLATFORMS = {
    "google": {"headline": 30, "description": 90, "path": 15, "min_headlines": 3, "max_headlines": 15,
               "min_descriptions": 2, "max_descriptions": 4, "hard": True},
    "microsoft": {"headline": 30, "description": 90, "path": 15, "min_headlines": 3, "max_headlines": 15,
                  "min_descriptions": 2, "max_descriptions": 4, "hard": True},
    "meta": {"headline": 40, "description": 30, "primary": 125, "hard": False},
    "linkedin": {"headline": 70, "primary": 150, "description": 100, "hard": False},
    "x": {"primary": 280, "headline": 70, "hard": True},
    "tiktok": {"primary": 100, "hard": True},
}
ACRONYM_OK = {"USA", "UK", "FAQ", "SEO", "PPC", "CEO", "API", "SaaS", "B2B", "B2C", "LLC", "NYC", "USB", "LED", "TV", "HD", "VIP", "AI", "PDF", "ROI", "CRM", "ERP"}
SUPERLATIVE = re.compile(r"\b(best|#1|number one|no\.? ?1|leading|world'?s? (?:best|leading|largest)|guaranteed?|cheapest|lowest price|fastest|top[- ]rated|perfect)\b", re.I)
PHONE = re.compile(r"(?<!\d)(\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3,4}[\s.-]?\d{3,4}(?!\d)")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆✅]")
PATH_OK = re.compile(r"^[A-Za-z0-9_\-]*$")


def ad_len(text):
    """Google counts full-width (CJK) characters as 2."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def _shout(text):
    words = re.findall(r"[A-Za-z]{3,}", text)
    return [w for w in words if w.isupper() and w not in ACRONYM_OK]


def _sim(a, b):
    sa, sb = set(tokenize(a)), set(tokenize(b))
    return len(sa & sb) / float(len(sa | sb)) if sa | sb else 0.0


def validate(platform, headlines, descriptions, paths=(), keywords=(), final_url="", primary=(), max_over=None):
    spec = dict(PLATFORMS[platform])
    if max_over:
        spec.update({k: v for k, v in max_over.items() if v})
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    rows = []
    sev_len = "error" if spec.get("hard") else "warning"

    def check_items(kind, items, limit):
        for n, t in enumerate(items, 1):
            L = ad_len(t)
            rows.append({"field": "%s %d" % (kind, n), "chars": L, "limit": limit, "status": "OK" if L <= limit else "TOO LONG", "text": t[:60]})
            if L > limit:
                add(sev_len, "TOO_LONG", "%s %d is %d chars (limit %d): %r" % (kind, n, L, limit, t[:40]))
            if not t.strip():
                add("error", "EMPTY", "%s %d is empty." % (kind, n))
    if headlines:
        check_items("headline", headlines, spec.get("headline", 9999))
    if descriptions:
        check_items("description", descriptions, spec.get("description", 9999))
    if primary:
        check_items("primary text", primary, spec.get("primary", 9999))
    if "min_headlines" in spec:
        if len(headlines) < spec["min_headlines"]:
            add("error", "TOO_FEW_HEADLINES", "%d headlines; %s needs at least %d." % (len(headlines), platform, spec["min_headlines"]))
        if len(headlines) > spec["max_headlines"]:
            add("error", "TOO_MANY_HEADLINES", "%d headlines; max is %d." % (len(headlines), spec["max_headlines"]))
        if len(descriptions) < spec["min_descriptions"]:
            add("error", "TOO_FEW_DESCRIPTIONS", "%d descriptions; need at least %d." % (len(descriptions), spec["min_descriptions"]))
        if len(descriptions) > spec["max_descriptions"]:
            add("error", "TOO_MANY_DESCRIPTIONS", "%d descriptions; max is %d." % (len(descriptions), spec["max_descriptions"]))
        if 0 < len(headlines) < 8:
            add("info", "MORE_HEADLINES", "Providing ~8-15 distinct headlines gives the system more combinations to test.")
    for n, p in enumerate(paths, 1):
        if p:
            if ad_len(p) > spec.get("path", 15):
                add("error", "PATH_TOO_LONG", "Display path %d is %d chars (limit %d)." % (n, ad_len(p), spec.get("path", 15)))
            if not PATH_OK.match(p):
                add("warning", "PATH_CHARS", "Display path %d %r should use letters, digits, hyphens or underscores only (no spaces)." % (n, p))
    allt = [("headline", t) for t in headlines] + [("description", t) for t in descriptions] + [("primary text", t) for t in primary]
    low = {}
    for kind, t in allt:
        low.setdefault(t.strip().lower(), []).append(kind)
    for t, kinds in low.items():
        if t and len(kinds) > 1:
            add("error" if platform in ("google", "microsoft") else "warning", "DUPLICATE", "Duplicate text: %r" % t[:50])
    for kind, t in allt:
        if kind == "headline" and "!" in t and platform in ("google", "microsoft"):
            add("error", "EXCLAMATION_IN_HEADLINE", "Exclamation marks are not allowed in headlines: %r" % t[:40])
        if re.search(r"([!?.,;:])\1+|\.{3,}", t):
            add("warning", "REPEATED_PUNCTUATION", "Repeated punctuation in %r" % t[:40])
        if _shout(t):
            add("warning", "EXCESS_CAPS", "ALL-CAPS word(s) %s in %r (often disapproved)." % (", ".join(_shout(t)[:3]), t[:40]))
        if EMOJI.search(t) and platform in ("google", "microsoft"):
            add("warning", "SYMBOLS", "Emoji/symbols in %r may violate editorial policy." % t[:40])
        if PHONE.search(t) and platform in ("google", "microsoft"):
            add("warning", "PHONE_IN_TEXT", "Phone number in ad text is disallowed; use call assets: %r" % t[:40])
        m = SUPERLATIVE.search(t)
        if m and platform in ("google", "microsoft"):
            add("info", "UNVERIFIED_CLAIM", "%r in %r may need verifiable support on the landing page." % (m.group(0), t[:40]))
        if t != t.strip() or "  " in t:
            add("info", "WHITESPACE", "Extra whitespace in %r" % t[:40])
    hl = headlines
    similar = [(i + 1, j + 1) for i in range(len(hl)) for j in range(i + 1, len(hl)) if hl[i].strip().lower() != hl[j].strip().lower() and _sim(hl[i], hl[j]) >= 0.8]
    if similar:
        add("warning", "SIMILAR_HEADLINES", "Headlines too similar to each other: %s. Vary the angle, not just a word." % ", ".join("%d~%d" % s for s in similar[:5]))
    kw_rows = []
    for k in keywords:
        kt = " ".join(tokenize(k))
        hits = [i + 1 for i, h in enumerate(hl) if kt and kt in " ".join(tokenize(h))]
        dh = [i + 1 for i, d in enumerate(descriptions) if kt and kt in " ".join(tokenize(d))]
        kw_rows.append({"keyword": k, "in_headlines": ",".join(map(str, hits)) or "-", "in_descriptions": ",".join(map(str, dh)) or "-"})
        if not hits:
            add("warning", "KEYWORD_NOT_IN_HEADLINE", "Keyword %r is in no headline (relevance and ad strength)." % k)
    if final_url and not re.match(r"^https://", final_url):
        add("warning", "FINAL_URL_NOT_HTTPS", "Final URL should be https.")
    return {"platform": platform, "fields": rows, "keyword_coverage": kw_rows, "issues": sort_issues(issues),
            "note": "Limits and policy checks are heuristics based on published specs; platforms change them, so confirm in the ad editor."}


def load_file(path):
    text = read_text_file(path)
    if path.lower().endswith(".json"):
        try:
            d = json.loads(text)
        except ValueError as e:
            raise ToolError("invalid JSON in %s: %s" % (path, e))
        g = lambda *keys: next((d[k] for k in keys if k in d), [])
        return {"headlines": g("headlines", "headline"), "descriptions": g("descriptions", "description"),
                "paths": g("paths", "path") or [d.get("path1", ""), d.get("path2", "")], "primary": g("primary", "primary_text")}
    out = {"headlines": [], "descriptions": [], "paths": [], "primary": []}
    for line in text.splitlines():
        m = re.match(r"^\s*(H|D|P|PATH|PRIMARY)\s*[:\-]\s*(.*)$", line, re.I)
        if m:
            k = m.group(1).upper()
            out[{"H": "headlines", "D": "descriptions", "P": "primary", "PRIMARY": "primary", "PATH": "paths"}[k]].append(m.group(2).strip())
    return out


def _args(p):
    p.add_argument("--platform", choices=sorted(PLATFORMS), default="google")
    p.add_argument("--file", help=".json ({headlines:[],descriptions:[],paths:[]}) or text file with 'H: ...', 'D: ...' lines")
    p.add_argument("-H", "--headline", action="append", default=[])
    p.add_argument("-D", "--description", action="append", default=[])
    p.add_argument("--primary", action="append", default=[], help="primary text (social platforms)")
    p.add_argument("--path", action="append", default=[], help="display path segment (max 2)")
    p.add_argument("--keyword", "-k", action="append", default=[])
    p.add_argument("--final-url", default="")
    p.add_argument("--max-headline", type=int)
    p.add_argument("--max-description", type=int)


def _run(a):
    d = {"headlines": list(a.headline), "descriptions": list(a.description), "paths": list(a.path), "primary": list(a.primary)}
    if a.file:
        f = load_file(a.file)
        for k in d:
            d[k] = f.get(k, []) + d[k]
    if not any(d.values()):
        raise ToolError("provide --file or -H/-D/--primary text")
    return validate(a.platform, d["headlines"], d["descriptions"], d["paths"], a.keyword, a.final_url, d["primary"],
                    {"headline": a.max_headline, "description": a.max_description})


TOOLS = [Tool("adcopy", "Ads / Copy, tracking & landing pages", "Validate ad copy: Google/Microsoft RSA limits & policy flags, Meta/LinkedIn/X/TikTok lengths, keyword coverage", _args, _run)]
