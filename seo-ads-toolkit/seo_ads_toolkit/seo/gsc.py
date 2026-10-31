"""Google Search Console export analyzer (queries/pages CSV): quick wins, CTR gaps, cannibalization, comparisons."""
from __future__ import annotations

import io
import statistics
import zipfile

from ..core import Tool, ToolError, find_col, read_table, read_text_file, to_number


def load(path, member=None):
    if path.lower().endswith(".zip"):
        try:
            z = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise ToolError("cannot open %s: %s" % (path, e))
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        pick = member or next((n for n in names if "quer" in n.lower()), None) or (names[0] if names else None)
        if not pick or pick not in z.namelist():
            raise ToolError("CSV member not found in zip; available: %s" % ", ".join(names))
        raw = z.read(pick)
        text = raw.decode("utf-8-sig", "replace")
    else:
        text = read_text_file(path)
    rows = read_table(text, is_text=True)
    if len(rows) < 2:
        raise ToolError("%s has no data rows" % path)
    header = rows[0]
    qi = find_col(header, "quer", "keyword")
    pi = find_col(header, "page", "url")
    ci = find_col(header, "click")
    ii = find_col(header, "impression", "impr")
    ti = find_col(header, "ctr")
    oi = find_col(header, "position", "pos")
    if ci is None or ii is None or (qi is None and pi is None):
        raise ToolError("could not find Clicks/Impressions and a Query or Page column in the header: %s" % header)
    out = []
    for r in rows[1:]:
        if len(r) <= max(c for c in (qi, pi, ci, ii, oi) if c is not None):
            continue
        clicks, impr = to_number(r[ci]), to_number(r[ii])
        if clicks is None or impr is None:
            continue
        pos = to_number(r[oi]) if oi is not None else None
        out.append({"query": r[qi].strip() if qi is not None else None, "page": r[pi].strip() if pi is not None else None,
                    "clicks": clicks, "impressions": impr, "ctr": clicks / impr if impr else 0.0, "position": pos})
    if not out:
        raise ToolError("no usable rows parsed")
    return out, {"query": qi is not None, "page": pi is not None, "position": oi is not None}


def _median(xs):
    return statistics.median(xs) if xs else None


def analyze(rows, cols, min_impr=100, brand=(), top=25):
    dim = "query" if cols["query"] else "page"
    brand = [b.strip().lower() for b in brand if b.strip()]
    is_brand = lambda r: bool(r["query"]) and any(b in r["query"].lower() for b in brand)
    tot_c, tot_i = sum(r["clicks"] for r in rows), sum(r["impressions"] for r in rows)
    pos_rows = [r for r in rows if r["position"] is not None]
    avg_pos = sum(r["position"] * r["impressions"] for r in pos_rows) / max(1.0, sum(r["impressions"] for r in pos_rows)) if pos_rows else None
    res = {"rows": len(rows), "total_clicks": int(tot_c), "total_impressions": int(tot_i),
           "overall_ctr_pct": round(100.0 * tot_c / tot_i, 2) if tot_i else 0,
           "avg_position_weighted": round(avg_pos, 2) if avg_pos else None,
           "thresholds": {"min_impressions": min_impr, "striking_distance_positions": "8-20",
                          "ctr_outlier_rule": "ctr < 50% of the median ctr of rows at the same rounded position (needs >=5 rows)"}}
    if brand and cols["query"]:
        b = [r for r in rows if is_brand(r)]
        res["brand_split"] = {"brand_clicks": int(sum(r["clicks"] for r in b)),
                              "nonbrand_clicks": int(sum(r["clicks"] for r in rows if not is_brand(r))),
                              "brand_share_pct": round(100.0 * sum(r["clicks"] for r in b) / tot_c, 1) if tot_c else 0}
    base = [r for r in pos_rows if r["impressions"] >= min_impr and not is_brand(r) and r["position"] <= 20]
    buckets = {}
    for r in base:
        buckets.setdefault(max(1, int(round(r["position"]))), []).append(r["ctr"])
    med = {k: _median(v) for k, v in buckets.items() if len(v) >= 5}
    res["own_median_ctr_by_position_pct"] = [{"position": k, "median_ctr_pct": round(100 * v, 2), "rows": len(buckets[k])} for k, v in sorted(med.items())]
    cols_out = lambda r: {dim: r[dim], "clicks": int(r["clicks"]), "impressions": int(r["impressions"]),
                          "ctr_pct": round(100 * r["ctr"], 2), "position": round(r["position"], 1) if r["position"] is not None else None}
    res["top_by_clicks"] = [cols_out(r) for r in sorted(rows, key=lambda r: -r["clicks"])[:top]]
    if pos_rows:
        sd = [r for r in pos_rows if 8 <= r["position"] <= 20 and r["impressions"] >= min_impr]
        sd.sort(key=lambda r: -r["impressions"])
        target = med.get(3)
        out = []
        for r in sd[:top]:
            d = cols_out(r)
            d["est_extra_clicks_at_pos3"] = int(max(0, (target - r["ctr"]) * r["impressions"])) if target is not None else None
            out.append(d)
        res["striking_distance"] = out
        outl = []
        for r in base:
            m = med.get(max(1, int(round(r["position"]))))
            if m and r["ctr"] < 0.5 * m:
                d = cols_out(r)
                d["expected_ctr_pct"] = round(100 * m, 2)
                d["missed_clicks"] = int((m - r["ctr"]) * r["impressions"])
                outl.append(d)
        outl.sort(key=lambda d: -d["missed_clicks"])
        res["ctr_underperformers"] = outl[:top]
    zero = [r for r in rows if r["clicks"] == 0 and r["impressions"] >= min_impr and (r["position"] is None or r["position"] <= 30)]
    res["impressions_no_clicks"] = [cols_out(r) for r in sorted(zero, key=lambda r: -r["impressions"])[:top]]
    if cols["query"] and cols["page"]:
        byq = {}
        for r in rows:
            if r["impressions"] >= max(10, min_impr // 10):
                byq.setdefault(r["query"], []).append(r)
        can = []
        for q, rs in byq.items():
            if len(rs) >= 2:
                rs.sort(key=lambda r: -r["impressions"])
                ti = sum(r["impressions"] for r in rs)
                if ti >= min_impr:
                    can.append({"query": q, "pages": len(rs), "total_impressions": int(ti), "total_clicks": int(sum(r["clicks"] for r in rs)),
                                "top_page_impr_share_pct": round(100.0 * rs[0]["impressions"] / ti, 1),
                                "pages_and_positions": " | ".join("%s @%s" % (r["page"], round(r["position"], 1) if r["position"] else "?") for r in rs[:4])})
        can.sort(key=lambda d: -d["total_impressions"])
        res["possible_cannibalization"] = can[:top]
        res["cannibalization_note"] = "Several URLs earning impressions for one query is a signal, not proof; check whether they target the same intent."
    return res


def compare(cur, prev, cols, top=25):
    dim = "query" if cols["query"] else "page"
    key = lambda r: (r["query"], r["page"]) if cols["query"] and cols["page"] else r[dim]
    a = {key(r): r for r in cur}
    b = {key(r): r for r in prev}
    rows = []
    for k in set(a) | set(b):
        c, p = a.get(k), b.get(k)
        cc, pc = (c["clicks"] if c else 0), (p["clicks"] if p else 0)
        rows.append({"item": " | ".join(x for x in k if x) if isinstance(k, tuple) else k, "clicks_now": int(cc), "clicks_before": int(pc),
                     "change": int(cc - pc), "status": "new" if not p else ("lost" if not c else "")})
    gain = sorted([r for r in rows if r["change"] > 0], key=lambda r: -r["change"])[:top]
    loss = sorted([r for r in rows if r["change"] < 0], key=lambda r: r["change"])[:top]
    return {"clicks_now": int(sum(r["clicks"] for r in cur)), "clicks_before": int(sum(r["clicks"] for r in prev)),
            "biggest_gains": gain, "biggest_losses": loss}


def _args(p):
    p.add_argument("file", help="Search Console export (.csv, or the .zip from the Export button)")
    p.add_argument("--compare", metavar="FILE", help="earlier period export (same dimension) for winners/losers")
    p.add_argument("--member", help="CSV name inside a zip export (default: Queries.csv)")
    p.add_argument("--min-impressions", type=int, default=100)
    p.add_argument("--brand", default="", help="comma-separated brand terms to separate branded queries")
    p.add_argument("--top", type=int, default=25)


def _run(a):
    rows, cols = load(a.file, a.member)
    res = analyze(rows, cols, a.min_impressions, a.brand.split(","), a.top)
    if a.compare:
        prev, pcols = load(a.compare, a.member)
        res["comparison"] = compare(rows, prev, cols, a.top)
    return res


TOOLS = [Tool("gsc", "SEO / Data & reporting", "Analyse Search Console CSV: striking-distance queries, CTR gaps, brand split, cannibalization, period compare", _args, _run)]
