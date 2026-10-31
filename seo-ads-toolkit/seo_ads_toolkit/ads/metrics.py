"""Ad performance calculators: CTR/CPC/CPA/ROAS/ROI, break-even, target bids, bid adjustments."""
from __future__ import annotations

from ..core import Tool, ToolError, issue, parse_ratio, safe_div, sort_issues, to_number


def _n(v, name):
    if v is None:
        return None
    x = to_number(v)
    if x is None:
        raise ToolError("--%s must be a number, got %r" % (name.replace("_", "-"), v))
    if x < 0:
        raise ToolError("--%s cannot be negative" % name.replace("_", "-"))
    return x


def compute(impressions=None, clicks=None, cost=None, conversions=None, revenue=None, margin=None,
            target_roas=None, target_cpa=None):
    r = {}
    ctr = safe_div(clicks, impressions)
    cvr = safe_div(conversions, clicks)
    cpc = safe_div(cost, clicks, 4)
    cpa = safe_div(cost, conversions, 4)
    roas = safe_div(revenue, cost)
    aov = safe_div(revenue, conversions, 4)
    r["ctr_pct"] = round(ctr * 100, 3) if ctr is not None else None
    r["cpc"] = cpc
    r["cpm"] = round(cost / impressions * 1000, 4) if cost is not None and impressions else None
    r["conversion_rate_pct"] = round(cvr * 100, 3) if cvr is not None else None
    r["cpa"] = cpa
    r["aov"] = aov
    r["roas"] = roas
    r["roas_pct"] = round(roas * 100, 1) if roas is not None else None
    if revenue is not None and cost is not None:
        r["net_after_ad_spend"] = round(revenue - cost, 2)
        r["roi_pct"] = round((revenue - cost) / cost * 100, 1) if cost else None
        if margin is not None:
            gross = revenue * margin
            r["gross_profit"] = round(gross, 2)
            r["profit_after_ad_spend"] = round(gross - cost, 2)
            r["profit_roi_pct"] = round((gross - cost) / cost * 100, 1) if cost else None
    if margin:
        r["break_even_roas"] = round(1.0 / margin, 3)
        if aov:
            r["break_even_cpa"] = round(aov * margin, 2)
            r["break_even_cpc"] = round(aov * margin * cvr, 4) if cvr else None
        if roas is not None:
            r["vs_break_even"] = "profitable at the margin given" if roas > 1.0 / margin else "losing money at the margin given"
    issues = []
    if target_roas and roas is not None:
        ok = roas >= target_roas
        r["target_roas_status"] = "%s (%.2f vs target %.2f)" % ("met" if ok else "MISSED", roas, target_roas)
        if not ok:
            issues.append(issue("warning", "ROAS_BELOW_TARGET", "ROAS %.2f is below target %.2f." % (roas, target_roas)))
    if target_cpa and cpa is not None:
        ok = cpa <= target_cpa
        r["target_cpa_status"] = "%s (%.2f vs target %.2f)" % ("met" if ok else "MISSED", cpa, target_cpa)
        if not ok:
            issues.append(issue("warning", "CPA_ABOVE_TARGET", "CPA %.2f is above target %.2f." % (cpa, target_cpa)))
    if conversions is not None and conversions < 30:
        issues.append(issue("info", "LOW_VOLUME", "Only %g conversions; ratios are noisy. Avoid big decisions below ~30-100 conversions." % conversions))
    r["issues"] = sort_issues(issues)
    return {k: v for k, v in r.items() if v is not None}


def _calc_args(p):
    p.add_argument("--impressions", "-i")
    p.add_argument("--clicks", "-c")
    p.add_argument("--cost", "--spend")
    p.add_argument("--conversions", "-v")
    p.add_argument("--revenue", "-r", help="conversion value / revenue")
    p.add_argument("--margin", help="gross margin, e.g. 30%% or 0.3 (enables break-even and profit)")
    p.add_argument("--target-roas", type=float)
    p.add_argument("--target-cpa", type=float)


def _calc_run(a):
    vals = {k: _n(getattr(a, k), k) for k in ("impressions", "clicks", "cost", "conversions", "revenue")}
    if all(v is None for v in vals.values()):
        raise ToolError("give at least two of --impressions --clicks --cost --conversions --revenue")
    if vals["clicks"] is not None and vals["impressions"] is not None and vals["clicks"] > vals["impressions"]:
        raise ToolError("clicks cannot exceed impressions")
    m = parse_ratio(a.margin) if a.margin else None
    return compute(margin=m, target_roas=a.target_roas, target_cpa=a.target_cpa, **vals)


# --------------------------------------------------------------------------- bids
def max_cpc(aov=None, cvr=None, target_roas=None, target_cpa=None, margin=None):
    """Maximum CPC that hits a target at the stated conversion rate."""
    out = {}
    if target_cpa:
        out["max_cpc_for_target_cpa"] = round(target_cpa * cvr, 4)
    if aov and target_roas:
        out["max_cpc_for_target_roas"] = round(aov * cvr / target_roas, 4)
    if aov and margin:
        out["break_even_cpc"] = round(aov * margin * cvr, 4)
    if aov and margin and not target_cpa:
        out["break_even_cpa"] = round(aov * margin, 2)
    return out


def apply_adjustments(base, adjustments):
    rows = []
    for item in adjustments:
        if "=" not in item:
            raise ToolError("--adjust must look like mobile=-20 or desktop=+10 (percent)")
        k, v = item.split("=", 1)
        pct = to_number(v)
        if pct is None:
            raise ToolError("bad percentage in --adjust %r" % item)
        rows.append({"segment": k.strip(), "adjustment_pct": pct, "effective_bid": round(base * (1 + pct / 100.0), 4)})
    return rows


def _bid_args(p):
    p.add_argument("--aov", help="average order value / lead value")
    p.add_argument("--cvr", help="click-to-conversion rate, e.g. 3%% or 0.03")
    p.add_argument("--target-roas", type=float, help="e.g. 4 for 400%%")
    p.add_argument("--target-cpa", type=float)
    p.add_argument("--margin", help="gross margin for break-even bid")
    p.add_argument("--base-bid", type=float, help="current bid; combine with --adjust")
    p.add_argument("--adjust", action="append", default=[], metavar="SEGMENT=PCT", help="bid adjustment, e.g. mobile=-20 (repeatable)")


def _bid_run(a):
    cvr = parse_ratio(a.cvr) if a.cvr else None
    out = {}
    if a.aov or a.target_cpa or a.margin:
        if not cvr:
            raise ToolError("--cvr is required to compute a max CPC")
        aov = _n(a.aov, "aov")
        out = max_cpc(aov, cvr, a.target_roas, a.target_cpa, parse_ratio(a.margin) if a.margin else None)
        if not out:
            raise ToolError("provide --target-cpa, or --aov with --target-roas / --margin")
        out["assumed_cvr_pct"] = round(cvr * 100, 3)
        out["formula"] = "max CPC = target CPA x CVR  =  AOV x CVR / target ROAS"
    if a.adjust:
        if not a.base_bid:
            raise ToolError("--adjust needs --base-bid")
        out["adjusted_bids"] = apply_adjustments(a.base_bid, a.adjust)
    if not out:
        raise ToolError("nothing to compute: pass --aov/--cvr/--target-* and/or --base-bid with --adjust")
    return out


TOOLS = [
    Tool("calc", "Ads / Math & testing", "CTR, CPC, CPM, CVR, CPA, ROAS, ROI, break-even ROAS/CPA and profit from raw numbers", _calc_args, _calc_run),
    Tool("bid", "Ads / Math & testing", "Max CPC for a target CPA/ROAS, break-even bid, bid-adjustment maths", _bid_args, _bid_run),
]
