"""Keyword analysis (n-grams, density, placement), intent classification, and autocomplete suggestions."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from collections import Counter

from ..core import (STOPWORDS, Tool, ToolError, add_net_args, decode_body, fetch, issue, load_page,
                    ngrams, read_text_file, sort_issues, tokenize)

INTENT_RULES = [
    ("transactional", r"\b(buy|purchase|order|price|prices|pricing|cost|cheap|cheapest|discount|coupon|deal|deals|sale|for sale|"
                      r"near me|hire|quote|book|booking|subscribe|download|free trial|shop|get)\b"),
    ("commercial", r"\b(best|top|review|reviews|vs|versus|compare|comparison|alternative|alternatives|rated|worth it|pros and cons)\b"),
    ("navigational", r"\b(login|log in|sign in|signin|official|website|app|portal|contact|customer service|support number)\b"),
    ("informational", r"\b(how|what|why|when|where|who|which|guide|tutorial|tips|ideas|examples?|meaning|definition|learn|"
                      r"explained|difference|can|does|is|are|should)\b"),
]


def classify_intent(kw):
    k = kw.lower()
    for label, rx in INTENT_RULES:
        if re.search(rx, k):
            return label
    return "unclear"


def analyze_text(text, top=15, keywords=()):
    tokens = tokenize(text)
    total = len(tokens)
    if total == 0:
        raise ToolError("no words found to analyse")
    content = [t for t in tokens if t not in STOPWORDS and len(t) > 2 and not t.isdigit()]
    uni = Counter(content)

    def good(g):
        w = g.split()
        return w[0] not in STOPWORDS and w[-1] not in STOPWORDS and not any(x.isdigit() for x in w)
    bi = Counter(g for g in ngrams(tokens, 2) if good(g))
    tri = Counter(g for g in ngrams(tokens, 3) if good(g))

    def table(counter, n, minc):
        return [{"phrase": k, "count": v, "density_pct": round(100.0 * v * n / total, 2)} for k, v in counter.most_common(top) if v >= minc]
    issues = []
    for row in table(uni, 1, 3)[:3]:
        if row["density_pct"] > 4:
            issues.append(issue("warning", "HIGH_DENSITY", "'%s' is %.1f%% of all words; check for over-optimisation." % (row["phrase"], row["density_pct"])))
    res = {"total_words": total, "unique_words": len(set(tokens)), "single_words": table(uni, 1, 2),
           "two_word_phrases": table(bi, 2, 2), "three_word_phrases": table(tri, 3, 2)}
    if keywords:
        low = " ".join(tokens)
        kr = []
        for k in keywords:
            kt = " ".join(tokenize(k))
            c = len(re.findall(r"(?<!\w)" + re.escape(kt) + r"(?!\w)", low)) if kt else 0
            kr.append({"keyword": k, "count": c, "density_pct": round(100.0 * c * max(1, len(kt.split())) / total, 2),
                       "intent": classify_intent(k)})
            if c == 0:
                issues.append(issue("warning", "KEYWORD_ABSENT", "'%s' does not appear in the text." % k))
        res["keywords"] = kr
    res["issues"] = sort_issues(issues)
    return res


def _args(p):
    p.add_argument("target", nargs="?", help="URL, .html file, or text file")
    p.add_argument("--text", help="analyse this text instead")
    p.add_argument("--keyword", "-k", action="append", default=[], help="keyword to measure (repeatable)")
    p.add_argument("--top", type=int, default=15)
    add_net_args(p)


def _run(a):
    if a.text:
        text = a.text
    elif not a.target:
        raise ToolError("give a URL/file or --text")
    elif os.path.isfile(a.target) and not a.target.lower().endswith((".html", ".htm")):
        text = read_text_file(a.target)
    else:
        text = load_page(a.target, a.ua, a.timeout)[0].main_text
    return analyze_text(text, a.top, a.keyword)


# --------------------------------------------------------------- suggestions
SUGGEST_ENDPOINT = "https://suggestqueries.google.com/complete/search"
QUESTION_WORDS = ["how", "what", "why", "when", "where", "who", "which", "can", "is", "are", "does", "should"]
MODIFIERS = ["for", "with", "without", "near", "to", "vs", "versus", "and", "or", "like", "best", "cheap", "free"]


def fetch_suggestions(query, lang="en", country="us", endpoint=SUGGEST_ENDPOINT, timeout=10.0):
    q = urllib.parse.urlencode({"client": "firefox", "q": query, "hl": lang, "gl": country})
    r = fetch(endpoint + "?" + q, timeout=timeout)
    if r.status != 200:
        raise ToolError("suggest endpoint returned %s %s" % (r.status or "", r.error))
    try:
        data = json.loads(decode_body(r.body, r.headers.get("content-type", "")))
        return [s for s in data[1] if isinstance(s, str)]
    except (ValueError, IndexError, TypeError):
        raise ToolError("unexpected response from suggest endpoint")


def _sug_args(p):
    p.add_argument("seed")
    p.add_argument("--alphabet", action="store_true", help="also query 'seed a' ... 'seed z'")
    p.add_argument("--questions", action="store_true", help="also query 'how seed', 'what seed', ...")
    p.add_argument("--modifiers", action="store_true", help="also query 'seed for', 'seed vs', ...")
    p.add_argument("--lang", default="en")
    p.add_argument("--country", default="us")
    p.add_argument("--sleep", type=float, default=0.4, help="pause between requests (be polite)")
    p.add_argument("--endpoint", default=SUGGEST_ENDPOINT, help=__import__("argparse").SUPPRESS)
    p.add_argument("--timeout", type=float, default=10.0)


def _sug_run(a):
    queries = [a.seed]
    if a.alphabet:
        queries += ["%s %s" % (a.seed, c) for c in "abcdefghijklmnopqrstuvwxyz"]
    if a.questions:
        queries += ["%s %s" % (w, a.seed) for w in QUESTION_WORDS]
    if a.modifiers:
        queries += ["%s %s" % (a.seed, m) for m in MODIFIERS]
    seen, out = set(), []
    for i, q in enumerate(queries):
        if i:
            time.sleep(a.sleep)
        try:
            sugg = fetch_suggestions(q, a.lang, a.country, a.endpoint, a.timeout)
        except ToolError as e:
            if i == 0:
                raise
            continue
        for s in sugg:
            if s.lower() not in seen:
                seen.add(s.lower())
                out.append({"suggestion": s, "intent": classify_intent(s), "from_query": q})
    return {"seed": a.seed, "count": len(out), "suggestions": out,
            "note": "Uses Google's unofficial autocomplete endpoint; it can change or rate-limit without notice."}


TOOLS = [
    Tool("keywords", "SEO / Content & keywords", "Keyword/n-gram frequency, density and placement for a page or text", _args, _run),
    Tool("suggest", "SEO / Content & keywords", "Keyword ideas from Google autocomplete (A-Z, questions, modifiers) with intent tags", _sug_args, _sug_run),
]
