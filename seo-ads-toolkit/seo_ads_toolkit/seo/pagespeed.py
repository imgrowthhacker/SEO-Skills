"""Page speed: offline HTML weight/render-blocking check + Google PageSpeed Insights API client."""
from __future__ import annotations

import json
import urllib.parse

from ..core import Tool, ToolError, add_net_args, fetch, issue, load_page, parse_html, sort_issues

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
# (good_upper, needs_improvement_upper) per Google's Core Web Vitals thresholds
THRESHOLDS = {"lcp_ms": (2500, 4000), "inp_ms": (200, 500), "cls": (0.1, 0.25), "fcp_ms": (1800, 3000),
              "ttfb_ms": (800, 1800), "tbt_ms": (200, 600)}


def rate(metric, value):
    if value is None or metric not in THRESHOLDS:
        return None
    good, ni = THRESHOLDS[metric]
    return "good" if value <= good else ("needs improvement" if value <= ni else "poor")


def quick_check(page, resp):
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    head_scripts = [s for s in page.scripts if s["src"] and s["in_head"] and not (s["async"] or s["defer"] or s["module"])]
    if head_scripts:
        add("warning", "RENDER_BLOCKING_JS", "%d script(s) in <head> without async/defer block rendering." % len(head_scripts),
            examples=[s["src"] for s in head_scripts[:3]])
    if len(page.stylesheets) > 5:
        add("info", "MANY_STYLESHEETS", "%d stylesheets; consider bundling/inlining critical CSS." % len(page.stylesheets))
    ext_scripts = [s for s in page.scripts if s["src"]]
    if len(ext_scripts) > 15:
        add("warning", "MANY_SCRIPTS", "%d external scripts." % len(ext_scripts))
    nodim = [i for i in page.images if not (i["width"] and i["height"])]
    if nodim:
        add("info", "IMG_NO_DIMENSIONS", "%d images without width/height risk layout shift (CLS)." % len(nodim))
    eager = [i for i in page.images[3:] if i["loading"] != "lazy"]
    if len(eager) > 3:
        add("info", "NO_LAZY_LOADING", "%d below-the-fold-ish images lack loading=\"lazy\"." % len(eager))
    legacy = [i["src"] for i in page.images if i["src"].lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".gif"))]
    if len(legacy) > 5:
        add("info", "LEGACY_IMAGE_FORMATS", "%d images are png/jpg/gif; WebP/AVIF are usually much smaller." % len(legacy))
    preloads = [t for t in page.link_tags if "preload" in t["rel"]]
    h = resp.headers if resp else {}
    if resp is not None:
        if resp.ttfb > 0.8:
            add("warning", "SLOW_TTFB", "TTFB %d ms (good <= 800 ms)." % (resp.ttfb * 1000))
        if h.get("content-encoding", "").lower() not in ("gzip", "br", "deflate", "zstd"):
            add("warning", "NO_COMPRESSION", "HTML is not compressed.")
        if len(resp.body) > 500 * 1024:
            add("warning", "HEAVY_HTML", "HTML document is %d KB." % (len(resp.body) // 1024))
    return {
        "ttfb_ms": int(resp.ttfb * 1000) if resp else None,
        "html_kb": round(len(resp.body) / 1024, 1) if resp else round(page.html_length / 1024, 1),
        "compression": h.get("content-encoding", "none") if resp else None,
        "scripts": len(page.scripts), "external_scripts": len(ext_scripts), "stylesheets": len(page.stylesheets),
        "images": len(page.images), "preload_hints": len(preloads), "render_blocking_scripts": len(head_scripts),
        "note": "Static HTML check only. Use --psi for Lighthouse lab data and Chrome UX Report field data.",
        "issues": sort_issues(issues),
    }


def parse_psi(data):
    """Extract metrics from a PageSpeed Insights v5 JSON response."""
    if "error" in data:
        e = data["error"]
        raise ToolError("PageSpeed API error %s: %s" % (e.get("code"), e.get("message")))
    lh = data.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    num = lambda k: (audits.get(k) or {}).get("numericValue")
    lab = {"performance_score": None, "lcp_ms": num("largest-contentful-paint"), "fcp_ms": num("first-contentful-paint"),
           "tbt_ms": num("total-blocking-time"), "cls": num("cumulative-layout-shift"),
           "speed_index_ms": num("speed-index"), "ttfb_ms": num("server-response-time")}
    sc = (lh.get("categories", {}).get("performance") or {}).get("score")
    if sc is not None:
        lab["performance_score"] = int(round(sc * 100))
    lab_rows = []
    for k, v in lab.items():
        if v is None:
            continue
        lab_rows.append({"metric": k, "value": round(v, 3) if isinstance(v, float) else v, "rating": rate(k, v) or "-"})
    field = []
    for scope, label in (("loadingExperience", "page"), ("originLoadingExperience", "origin")):
        m = (data.get(scope) or {}).get("metrics") or {}
        for key, name in (("LARGEST_CONTENTFUL_PAINT_MS", "lcp_ms"), ("INTERACTION_TO_NEXT_PAINT", "inp_ms"),
                          ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "cls"), ("FIRST_CONTENTFUL_PAINT_MS", "fcp_ms"),
                          ("EXPERIMENTAL_TIME_TO_FIRST_BYTE", "ttfb_ms")):
            if key in m:
                pct = m[key].get("percentile")
                if name == "cls" and pct is not None:
                    pct = pct / 100.0
                field.append({"scope": label, "metric": name, "p75": pct, "category": m[key].get("category", "").lower()})
    opps = []
    for aid, a in audits.items():
        d = a.get("details") or {}
        if d.get("type") == "opportunity" and (d.get("overallSavingsMs") or 0) > 0:
            opps.append({"opportunity": a.get("title"), "savings_ms": int(d["overallSavingsMs"]),
                         "savings_kb": int((d.get("overallSavingsBytes") or 0) / 1024)})
    opps.sort(key=lambda x: -x["savings_ms"])
    issues = []
    for r in lab_rows:
        if r["rating"] == "poor":
            issues.append(issue("warning", "LAB_" + r["metric"].upper(), "%s = %s (poor)" % (r["metric"], r["value"])))
    for r in field:
        if r["category"] == "slow":
            issues.append(issue("warning", "FIELD_" + r["metric"].upper(), "%s p75 = %s (%s, real-user data)" % (r["metric"], r["p75"], r["scope"])))
    return {"lab_data": lab_rows, "field_data_crux": field or "no field data available for this URL/origin",
            "top_opportunities": opps[:10], "issues": sort_issues(issues)}


def run_psi(url, strategy="mobile", key=None, endpoint=PSI_ENDPOINT, timeout=120.0):
    q = [("url", url), ("strategy", strategy), ("category", "performance")]
    if key:
        q.append(("key", key))
    r = fetch(endpoint + "?" + urllib.parse.urlencode(q), timeout=timeout)
    if r.status == 0:
        raise ToolError("PageSpeed API unreachable: %s" % r.error)
    try:
        data = json.loads(r.text)
    except ValueError:
        raise ToolError("PageSpeed API returned non-JSON (HTTP %d)" % r.status)
    if r.status == 429:
        raise ToolError("PageSpeed API rate limit hit; retry later or pass --key with a free API key.")
    return parse_psi(data)


def _args(p):
    p.add_argument("url")
    p.add_argument("--psi", action="store_true", help="also call the Google PageSpeed Insights API (needs internet)")
    p.add_argument("--strategy", choices=["mobile", "desktop"], default="mobile")
    p.add_argument("--key", help="PageSpeed Insights API key (free; avoids shared-quota limits)")
    p.add_argument("--psi-endpoint", default=PSI_ENDPOINT, help=argparse_suppress())
    add_net_args(p)


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS


def _run(a):
    page, resp = load_page(a.url, a.ua, a.timeout)
    res = {"url": resp.final_url if resp else a.url, "static_check": quick_check(page, resp)}
    issues = list(res["static_check"]["issues"])
    if a.psi:
        psi = run_psi(a.url, a.strategy, a.key, a.psi_endpoint)
        res["pagespeed_insights"] = psi
        issues += psi["issues"]
    res["issues"] = sort_issues(issues)
    return res


TOOLS = [Tool("pagespeed", "SEO / Technical", "Page speed: static weight/render-blocking check + Google PageSpeed Insights (CWV) API", _args, _run)]
