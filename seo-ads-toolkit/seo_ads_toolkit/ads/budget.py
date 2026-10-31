"""Budget pacing and spend/conversion forecasting."""
from __future__ import annotations

import calendar
import datetime

from ..core import Tool, ToolError, issue, parse_ratio, sort_issues, to_number

AVG_DAYS = 30.4  # Google Ads' monthly spend cap is 30.4 x the average daily budget


def pacing(monthly, spent, day, days_in_month):
    if not 1 <= day <= days_in_month:
        raise ToolError("day must be between 1 and %d" % days_in_month)
    elapsed_pct = day / float(days_in_month)
    expected = monthly * elapsed_pct
    daily_run_rate = spent / day
    projected = daily_run_rate * days_in_month
    remaining_days = days_in_month - day
    remaining_budget = monthly - spent
    issues = []
    if projected > monthly * 1.05:
        issues.append(issue("warning", "OVERPACING", "At the current run rate you will spend %.0f, %.0f%% over budget." % (projected, (projected / monthly - 1) * 100)))
    elif projected < monthly * 0.9 and day >= 5:
        issues.append(issue("info", "UNDERPACING", "At the current run rate you will leave %.0f unspent (%.0f%%)." % (monthly - projected, (1 - projected / monthly) * 100)))
    return {"monthly_budget": monthly, "spent_so_far": spent, "day": day, "days_in_month": days_in_month,
            "month_elapsed_pct": round(elapsed_pct * 100, 1), "budget_used_pct": round(spent / monthly * 100, 1),
            "expected_spend_by_today": round(expected, 2), "variance_vs_expected": round(spent - expected, 2),
            "current_daily_run_rate": round(daily_run_rate, 2), "projected_month_end_spend": round(projected, 2),
            "remaining_budget": round(remaining_budget, 2),
            "daily_budget_needed_for_remaining_days": round(remaining_budget / remaining_days, 2) if remaining_days else None,
            "issues": sort_issues(issues)}


def forecast(monthly, cpc, cvr=None, aov=None, ctr=None, cpm=None):
    clicks = monthly / cpc
    res = {"monthly_budget": monthly, "avg_cpc": cpc, "estimated_clicks": int(clicks)}
    if ctr:
        res["estimated_impressions"] = int(clicks / ctr)
    if cvr:
        conv = clicks * cvr
        res["estimated_conversions"] = round(conv, 1)
        res["estimated_cpa"] = round(monthly / conv, 2) if conv else None
        if aov:
            res["estimated_revenue"] = round(conv * aov, 2)
            res["estimated_roas"] = round(conv * aov / monthly, 2)
    return res


def _args(p):
    p.add_argument("--monthly", type=float, required=True, help="monthly budget")
    p.add_argument("--spent", type=float, help="spend so far this month (enables pacing)")
    p.add_argument("--day", type=int, help="day of month (default: today)")
    p.add_argument("--days-in-month", type=int, help="default: current month length")
    p.add_argument("--cpc", type=float, help="average CPC (enables forecast)")
    p.add_argument("--cvr", help="click-to-conversion rate e.g. 3%%")
    p.add_argument("--ctr", help="CTR e.g. 4%% (adds impressions estimate)")
    p.add_argument("--aov", type=float, help="average order/lead value (adds revenue & ROAS)")
    p.add_argument("--scenarios", action="store_true", help="show forecast at 50%%, 75%%, 100%%, 150%%, 200%% of budget")


def _run(a):
    if a.monthly <= 0:
        raise ToolError("--monthly must be positive")
    today = datetime.date.today()
    dim = a.days_in_month or calendar.monthrange(today.year, today.month)[1]
    res = {"suggested_daily_budget": round(a.monthly / AVG_DAYS, 2),
           "daily_budget_note": "Google Ads may spend up to 2x the daily budget on a given day but caps the month at 30.4 x daily budget."}
    if a.spent is not None:
        res["pacing"] = pacing(a.monthly, a.spent, a.day or today.day, dim)
        res["issues"] = res["pacing"].pop("issues")
    if a.cpc:
        cvr = parse_ratio(a.cvr) if a.cvr else None
        ctr = parse_ratio(a.ctr) if a.ctr else None
        res["forecast"] = forecast(a.monthly, a.cpc, cvr, a.aov, ctr)
        if a.scenarios:
            res["scenarios"] = []
            for m in (0.5, 0.75, 1.0, 1.5, 2.0):
                f = forecast(a.monthly * m, a.cpc, cvr, a.aov, ctr)
                res["scenarios"].append({"budget": round(a.monthly * m, 2), "clicks": f["estimated_clicks"],
                                         "conversions": f.get("estimated_conversions"), "cpa": f.get("estimated_cpa"), "roas": f.get("estimated_roas")})
            res["scenario_note"] = "Linear model: real CPCs usually rise as you scale because the best auctions are bought first."
    return res


TOOLS = [Tool("budget", "Ads / Math & testing", "Monthly budget pacing + click/conversion/ROAS forecast with scale scenarios", _args, _run)]
