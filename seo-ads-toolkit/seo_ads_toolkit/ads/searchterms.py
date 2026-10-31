"""Search-terms report analyzer (Google/Microsoft Ads CSV exports): wasted spend and negative candidates."""
from __future__ import annotations

from collections import defaultdict

from ..core import STOPWORDS, Tool, ToolError, find_col, read_table, tokenize, to_number


def load(path):
    rows = read_table(path)
    hi = next((i for i, r in enumerate(rows[:15]) if len(r) >= 3 and any("search term" in c.lower() or c.strip().lower() == "query" for c in r)), None)
    if hi is None:
        raise ToolError("could not find a 'Search term' header row in %s" % path)
    h = rows[hi]
    ti = find_col(h, "search term", "query")
    ci = find_col(h, "clicks")
    xi = find_col(h, "impr")
    ki = find_col(h, "cost", exclude=("/", "per", "avg"))
    vi = find_col(h, "conversions", "conv.", exclude=("rate", "value", "/", "per", "cost"))
    ri = find_col(h, "conv. value", "conversion value", "revenue", "all conv. value")
    if ti is None or ci is None or ki is None:
        raise ToolError("need Search term, Clicks and Cost columns; found: %s" % h)
    out = []
    for r in rows[hi + 1:]:
        if len(r) <= max(ti, ci, ki) or not r[ti].strip() or r[ti].strip().lower().startswith(("total", "--")):
            continue
        out.append({"term": r[ti].strip().lower(), "clicks": to_number(r[ci]) or 0.0, "impr": (to_number(r[xi]) or 0.0) if xi is not None else 0.0,
                    "cost": to_number(r[ki]) or 0.0, "conv": (to_number(r[vi]) or 0.0) if vi is not None and vi < len(r) else 0.0,
                    "value": (to_number(r[ri]) or 0.0) if ri is not None and ri < len(r) else 0.0})
    if not out:
        raise ToolError("no data rows found")
    return out


def analyze(rows, min_clicks=10, min_cost=0.0, target_cpa=None, top=25):
    tc, tk, tv = sum(r["cost"] for r in rows), sum(r["clicks"] for r in rows), sum(r["conv"] for r in rows)
    waste = [r for r in rows if r["conv"] == 0 and (r["clicks"] >= min_clicks or (min_cost and r["cost"] >= min_cost))]
    waste.sort(key=lambda r: -r["cost"])
    wcost = sum(r["cost"] for r in rows if r["conv"] == 0)
    fmt = lambda r: {"search_term": r["term"], "clicks": int(r["clicks"]), "cost": round(r["cost"], 2), "conversions": round(r["conv"], 2),
                     "cpa": round(r["cost"] / r["conv"], 2) if r["conv"] else None}
    grams = defaultdict(lambda: {"cost": 0.0, "clicks": 0.0, "conv": 0.0, "terms": 0})
    for r in rows:
        toks = [t for t in tokenize(r["term"]) if t not in STOPWORDS]
        for n in (1, 2):
            for g in {" ".join(toks[i:i + n]) for i in range(len(toks) - n + 1)}:
                d = grams[g]
                d["cost"] += r["cost"]
                d["clicks"] += r["clicks"]
                d["conv"] += r["conv"]
                d["terms"] += 1
    bad = [(g, d) for g, d in grams.items() if d["conv"] == 0 and d["clicks"] >= min_clicks and d["terms"] >= 2]
    bad.sort(key=lambda x: -x[1]["cost"])
    res = {"search_terms": len(rows), "total_cost": round(tc, 2), "total_clicks": int(tk), "total_conversions": round(tv, 2),
           "cost_on_zero_conversion_terms": round(wcost, 2), "zero_conv_share_of_spend_pct": round(100 * wcost / tc, 1) if tc else 0,
           "method": "Zero-conversion terms with >= %d clicks%s are negative candidates; word/bigram table needs >= 2 terms and >= %d clicks." % (min_clicks, " or cost >= %s" % min_cost if min_cost else "", min_clicks),
           "wasted_spend_terms": [fmt(r) for r in waste[:top]],
           "negative_word_candidates": [{"word_or_phrase": g, "terms_containing": d["terms"], "clicks": int(d["clicks"]), "cost": round(d["cost"], 2)} for g, d in bad[:top]],
           "top_converting_terms": [fmt(r) for r in sorted([r for r in rows if r["conv"] > 0], key=lambda r: -r["conv"])[:top]]}
    if target_cpa:
        res["terms_over_2x_target_cpa"] = [fmt(r) for r in sorted(rows, key=lambda r: -r["cost"]) if r["conv"] and r["cost"] / r["conv"] > 2 * target_cpa][:top]
    res["note"] = "Review before adding negatives: a term with no conversions in a short window may just lack data."
    return res


def _args(p):
    p.add_argument("file", help="search terms report CSV/TSV")
    p.add_argument("--min-clicks", type=int, default=10)
    p.add_argument("--min-cost", type=float, default=0.0)
    p.add_argument("--target-cpa", type=float)
    p.add_argument("--top", type=int, default=25)


def _run(a):
    return analyze(load(a.file), a.min_clicks, a.min_cost, a.target_cpa, a.top)


TOOLS = [Tool("searchterms", "Ads / Keywords & search terms", "Analyse search-terms report: wasted spend, negative keyword candidates, top converters", _args, _run)]
