"""Internal link graph: PageRank, orphan/weak pages, click depth."""
from __future__ import annotations

from ..core import Tool, ToolError, add_net_args, normalize_url, read_lines
from .crawler import Crawler


def pagerank(graph, damping=0.85, iterations=60, tol=1e-9):
    """graph: {node: [targets]}. Returns {node: score} summing to 1."""
    nodes = set(graph)
    for outs in graph.values():
        nodes.update(outs)
    n = len(nodes)
    if n == 0:
        return {}
    pr = {v: 1.0 / n for v in nodes}
    out = {v: [t for t in set(graph.get(v, [])) if t != v] for v in nodes}
    for _ in range(iterations):
        dangling = sum(pr[v] for v in nodes if not out[v])
        new = {v: (1 - damping) / n + damping * dangling / n for v in nodes}
        for v in nodes:
            if out[v]:
                share = damping * pr[v] / len(out[v])
                for t in out[v]:
                    new[t] += share
        diff = sum(abs(new[v] - pr[v]) for v in nodes)
        pr = new
        if diff < tol:
            break
    return pr


def analyze_graph(crawler, important=None, follow_only=True):
    graph = {}
    for src, edges in crawler.edges.items():
        graph[src] = [t for t, _txt, nf in edges if not (follow_only and nf)]
    crawled = {r["url"]: r for r in crawler.results}
    graph = {s: [t for t in ts if t in crawled] for s, ts in graph.items() if s in crawled}
    pr = pagerank(graph)
    mx = max(pr.values()) if pr else 1
    rows = []
    for url, r in crawled.items():
        if r["status"] != 200:
            continue
        rows.append({"url": url, "pagerank_0_100": round(100 * pr.get(url, 0) / mx, 1), "inlinks": r["inlinks"],
                     "outlinks": len(set(graph.get(url, []))), "depth": r["depth"]})
    rows.sort(key=lambda x: -x["pagerank_0_100"])
    res = {"pages_analysed": len(rows), "top_pages_by_internal_pagerank": rows[:25],
           "weakly_linked_pages_le1_inlink": [r for r in rows if r["inlinks"] <= 1 and r["depth"] > 0][:25],
           "pages_with_no_outlinks": [r["url"] for r in rows if r["outlinks"] == 0][:25]}
    if important:
        rank = {r["url"]: i + 1 for i, r in enumerate(rows)}
        imp = []
        for u in important:
            n = normalize_url(u)
            row = next((r for r in rows if r["url"] == n), None)
            imp.append({"url": u, "found_in_crawl": bool(row), "rank": rank.get(n), "inlinks": row["inlinks"] if row else None,
                        "pagerank_0_100": row["pagerank_0_100"] if row else None,
                        "advice": ("not reached by the crawl: link to it from a strong page" if not row else
                                   "add more internal links" if row["inlinks"] < 3 else "ok")})
        res["important_pages"] = imp
    res["note"] = "Scores are relative, computed from internal links in this crawl only; not Google's PageRank."
    return res


def _args(p):
    p.add_argument("url")
    p.add_argument("--max-pages", type=int, default=200)
    p.add_argument("--max-depth", type=int, default=6)
    p.add_argument("--important", metavar="FILE", help="file of URLs you care most about; shows how well linked they are")
    p.add_argument("--include-nofollow", action="store_true", help="count nofollow links as edges")
    add_net_args(p)


def _run(a):
    c = Crawler(a.url, a.max_pages, a.max_depth, 4, 0.0, a.ua, a.timeout)
    c.crawl()
    if not c.results:
        raise ToolError("nothing crawled (blocked by robots.txt or unreachable?)")
    res = analyze_graph(c, read_lines(a.important) if a.important else None, not a.include_nofollow)
    res["truncated"] = c.truncated
    return res


TOOLS = [Tool("linkgraph", "SEO / Technical", "Internal-link analysis: PageRank, weakly linked pages, click depth, priority-page check", _args, _run)]
