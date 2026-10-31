"""Near-duplicate content detection (shingle Jaccard + SimHash)."""
from __future__ import annotations

import hashlib
import os
import zlib
from collections import Counter

from ..core import Tool, ToolError, add_net_args, load_page, read_text_file, tokenize


def shingles(tokens, k=5):
    if len(tokens) < k:
        return {zlib.crc32(" ".join(tokens).encode())} if tokens else set()
    return {zlib.crc32(" ".join(tokens[i:i + k]).encode()) for i in range(len(tokens) - k + 1)}


def jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / float(len(a | b)) if (a or b) else 0.0


def simhash(tokens, bits=64):
    v = [0] * bits
    for tok, w in Counter(tokens).items():
        h = int.from_bytes(hashlib.md5(tok.encode("utf-8")).digest()[:8], "big")
        for i in range(bits):
            v[i] += w if (h >> i) & 1 else -w
    out = 0
    for i in range(bits):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a, b):
    return bin(a ^ b).count("1")


def verdict(j):
    if j >= 0.9:
        return "near-identical"
    if j >= 0.7:
        return "very similar"
    if j >= 0.5:
        return "overlapping"
    return "different"


def compare(items, threshold=0.5, k=5):
    """items: list of (name, text). Returns pair rows at/above threshold."""
    docs = []
    for name, text in items:
        toks = tokenize(text)
        docs.append((name, len(toks), shingles(toks, k), simhash(toks)))
    pairs = []
    for i in range(len(docs)):
        for j in range(i + 1, len(docs)):
            sim = jaccard(docs[i][2], docs[j][2])
            if sim >= threshold:
                pairs.append({"a": docs[i][0], "b": docs[j][0], "similarity": round(sim, 3), "verdict": verdict(sim),
                              "simhash_distance": hamming(docs[i][3], docs[j][3]),
                              "words_a": docs[i][1], "words_b": docs[j][1]})
    pairs.sort(key=lambda r: -r["similarity"])
    return pairs


def _args(p):
    p.add_argument("sources", nargs="*", help="URLs or files (.html or text) to compare against each other")
    p.add_argument("--crawl", metavar="URL", help="crawl a site and compare all its pages")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--threshold", type=float, default=0.5, help="minimum Jaccard similarity to report (0-1)")
    p.add_argument("--shingle", type=int, default=5, help="words per shingle")
    p.add_argument("--min-words", type=int, default=50, help="ignore pages with fewer words")
    add_net_args(p)


def _run(a):
    items = []
    if a.crawl:
        from .crawler import Crawler
        c = Crawler(a.crawl, a.max_pages, 6, 4, 0.0, a.ua, a.timeout)
        c.crawl()
        items = [(u, p.main_text) for u, p in c.pages.items() if not p.noindex]
    for s in a.sources:
        if os.path.isfile(s) and not s.lower().endswith((".html", ".htm")):
            items.append((s, read_text_file(s)))
        else:
            items.append((s, load_page(s, a.ua, a.timeout)[0].main_text))
    if len(items) < 2:
        raise ToolError("need at least two sources (or --crawl)")
    items = [(n, t) for n, t in items if len(tokenize(t)) >= a.min_words]
    pairs = compare(items, a.threshold, a.shingle)
    issues = []
    from ..core import issue
    for r in pairs:
        if r["similarity"] >= 0.7:
            issues.append(issue("warning", "DUPLICATE_CONTENT", "%s ~ %s (%.0f%% similar)" % (r["a"], r["b"], r["similarity"] * 100)))
    return {"documents_compared": len(items), "pairs_reported": len(pairs), "pairs": pairs[:200], "issues": issues,
            "how_to_read": "Jaccard similarity of %d-word shingles. >=0.9 near-identical, >=0.7 very similar. Fix with canonical tags, 301s, or rewriting." % a.shingle}


TOOLS = [Tool("duplicates", "SEO / Content & keywords", "Find near-duplicate pages/files (shingle Jaccard + SimHash), or crawl a whole site", _args, _run)]
