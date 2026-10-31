"""Ads keyword tools: permutation builder, negative-keyword conflict checker, clustering."""
from __future__ import annotations

import itertools
import os
import re
from collections import Counter, defaultdict

from ..core import STOPWORDS, Tool, ToolError, issue, read_lines, sort_issues, stem, to_number, tokenize
from ..seo.keywords import classify_intent

PRESET_NEGATIVES = {
    "jobs": ["jobs", "job", "career", "careers", "hiring", "salary", "internship", "resume", "vacancy"],
    "free": ["free", "freebie", "torrent", "crack", "pirated"],
    "diy": ["diy", "tutorial", "how to make", "template", "course", "pdf"],
    "research": ["definition", "meaning", "wiki", "wikipedia", "history", "essay", "ppt"],
    "complaints": ["complaint", "scam", "lawsuit", "refund", "fraud"],
    "used": ["used", "second hand", "secondhand", "wholesale", "rental", "rent"],
}


def permute(lists, match=("broad",), limit=50000):
    total = 1
    for l in lists:
        total *= len(l)
    if total > limit:
        raise ToolError("%d combinations exceeds --limit %d; shorten the lists or raise the limit" % (total, limit))
    rows = []
    for combo in itertools.product(*lists):
        kw = " ".join(c for c in combo if c).strip()
        for m in match:
            rows.append({"keyword": {"broad": kw, "phrase": '"%s"' % kw, "exact": "[%s]" % kw}[m], "match_type": m})
    return rows


def _parse_neg(entry):
    e = entry.strip()
    if e.startswith("[") and e.endswith("]"):
        return "exact", tokenize(e[1:-1])
    if e.startswith('"') and e.endswith('"'):
        return "phrase", tokenize(e[1:-1])
    return "broad", tokenize(e.lstrip("-"))


def blocks(neg_entry, query):
    kind, nt = _parse_neg(neg_entry)
    qt = tokenize(query)
    if not nt:
        return False
    if kind == "exact":
        return qt == nt
    if kind == "phrase":
        return any(qt[i:i + len(nt)] == nt for i in range(len(qt) - len(nt) + 1))
    return all(t in qt for t in nt)  # broad negative: all words, any order (no close variants)


def check_conflicts(keywords, negatives):
    conf = []
    for n in negatives:
        for k in keywords:
            if blocks(n, k):
                conf.append({"negative": n, "blocks_keyword": k})
    issues = [issue("error", "NEGATIVE_BLOCKS_KEYWORD", "Negative %s blocks your keyword %r" % (c["negative"], c["blocks_keyword"])) for c in conf]
    return {"keywords": len(keywords), "negatives": len(negatives), "conflicts": conf, "issues": sort_issues(issues)}


def cluster(keywords, max_size=0):
    stems = {k: [stem(t) for t in tokenize(k) if t not in STOPWORDS] or tokenize(k) for k in keywords}
    df = Counter(t for ts in stems.values() for t in set(ts))
    n = len(keywords)
    universal = {t for t, c in df.items() if c == n} if n > 2 else set()
    groups = defaultdict(list)
    for k, ts in stems.items():
        cand = [t for t in set(ts) if t not in universal] or list(set(ts))
        head = sorted(cand, key=lambda t: (-df[t], -len(t), t))[0] if cand else "(none)"
        groups[head].append(k)
    if max_size:
        for head in list(groups):
            kws = groups[head]
            if len(kws) > max_size:
                sub = Counter(t for k in kws for t in set(stems[k]) if t != head)
                groups.pop(head)
                for k in kws:
                    cand = [t for t in set(stems[k]) if t != head]
                    second = sorted(cand, key=lambda t: (-sub[t], t))[0] if cand else ""
                    groups[(head + " " + second).strip()].append(k)
    return [{"ad_group": h, "keywords": len(v), "intent": Counter(classify_intent(k) for k in v).most_common(1)[0][0],
             "examples": " | ".join(v[:6])} for h, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


def _lists(items):
    out = []
    for it in items:
        out.append(read_lines(it) if os.path.isfile(it) else [x.strip() for x in it.split(",")])
        out[-1] = [x for x in out[-1] if x is not None]
    return out


def _perm_args(p):
    p.add_argument("--list", "-l", action="append", required=True, dest="lists", help="comma-separated words or a file; repeat per slot. Use ',' for an optional blank, e.g. -l 'buy,order' -l 'shoes' -l ',online'")
    p.add_argument("--match", default="broad", help="comma list of broad,phrase,exact")
    p.add_argument("--limit", type=int, default=50000)


def _perm_run(a):
    lists = []
    for it in a.lists:
        lists.append(read_lines(it) if os.path.isfile(it) else [x.strip() for x in it.split(",")])
    match = [m.strip() for m in a.match.split(",") if m.strip()]
    if not set(match) <= {"broad", "phrase", "exact"}:
        raise ToolError("--match must be broad, phrase and/or exact")
    rows = permute(lists, match, a.limit)
    return {"count": len(rows), "keywords": rows, "_raw": "\n".join(r["keyword"] for r in rows)}


def _neg_args(p):
    p.add_argument("--keywords", help="file of your keywords (one per line)")
    p.add_argument("--negatives", help="file of negative keywords (broad, \"phrase\", [exact])")
    p.add_argument("--preset", choices=sorted(PRESET_NEGATIVES) + ["all"], help="print a starter negative list (review before using!)")


def _neg_run(a):
    res = {}
    negs = read_lines(a.negatives) if a.negatives else []
    if a.preset:
        names = sorted(PRESET_NEGATIVES) if a.preset == "all" else [a.preset]
        pre = [w for n in names for w in PRESET_NEGATIVES[n]]
        res["preset_negatives"] = pre
        res["preset_note"] = "Starting points only: 'free', 'used' or 'rent' may be valid for your business."
        negs += pre
    if a.keywords:
        res.update(check_conflicts(read_lines(a.keywords), negs))
    elif not a.preset:
        raise ToolError("pass --preset and/or --keywords with --negatives")
    return res


def _cl_args(p):
    p.add_argument("file", help="keywords, one per line")
    p.add_argument("--max-size", type=int, default=0, help="split groups bigger than this by a second shared term")


def _cl_run(a):
    kws = list(dict.fromkeys(l.split(",")[0].strip() if to_number(l.split(",")[-1]) is not None and "," in l else l for l in read_lines(a.file)))
    if not kws:
        raise ToolError("no keywords in %s" % a.file)
    groups = cluster(kws, a.max_size)
    return {"keywords": len(kws), "ad_groups_suggested": len(groups), "groups": groups}


TOOLS = [
    Tool("permute", "Ads / Keywords & search terms", "Combine word lists into keyword permutations with broad/phrase/exact formatting", _perm_args, _perm_run),
    Tool("negatives", "Ads / Keywords & search terms", "Negative keyword presets + check that negatives don't block your own keywords", _neg_args, _neg_run),
    Tool("cluster", "Ads / Keywords & search terms", "Group keywords into ad-group candidates by shared head term, tagged with search intent", _cl_args, _cl_run),
]
