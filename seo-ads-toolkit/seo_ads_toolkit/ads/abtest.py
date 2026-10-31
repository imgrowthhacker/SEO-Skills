"""A/B test statistics: z-test, Fisher exact, Bayesian probability, sample-size planning."""
from __future__ import annotations

import math
import random
from statistics import NormalDist

from ..core import Tool, ToolError, issue, parse_ratio, sort_issues

N = NormalDist()


def z_test(xa, na, xb, nb, tails=2):
    pa, pb = xa / na, xb / nb
    pool = (xa + xb) / float(na + nb)
    se = math.sqrt(pool * (1 - pool) * (1.0 / na + 1.0 / nb))
    z = (pb - pa) / se if se > 0 else 0.0
    p = 2 * (1 - N.cdf(abs(z))) if tails == 2 else 1 - N.cdf(z)
    return z, p


def _lchoose(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_exact(xa, na, xb, nb):
    """Two-sided Fisher exact p-value for a 2x2 table (successes xa/na vs xb/nb)."""
    k, n, K, total = xa, na, xa + xb, na + nb

    def pmf(i):
        return math.exp(_lchoose(K, i) + _lchoose(total - K, n - i) - _lchoose(total, n))
    lo, hi = max(0, K - (total - n)), min(K, n)
    p0 = pmf(k)
    return min(1.0, sum(pmf(i) for i in range(lo, hi + 1) if pmf(i) <= p0 * (1 + 1e-9)))


def bayes(xa, na, xb, nb, draws=40000, seed=7):
    rng = random.Random(seed)
    wins, loss_b, lift_sum = 0, 0.0, 0.0
    for _ in range(draws):
        a = rng.betavariate(1 + xa, 1 + na - xa)
        b = rng.betavariate(1 + xb, 1 + nb - xb)
        if b > a:
            wins += 1
        else:
            loss_b += a - b
        lift_sum += (b - a) / a if a else 0
    return wins / float(draws), loss_b / draws, lift_sum / draws


def analyze(xa, na, xb, nb, alpha=0.05, tails=2):
    for name, x, n in (("A", xa, na), ("B", xb, nb)):
        if n <= 0:
            raise ToolError("variant %s needs visitors > 0" % name)
        if x < 0 or x > n:
            raise ToolError("variant %s conversions must be between 0 and visitors" % name)
    pa, pb = xa / float(na), xb / float(nb)
    z, p = z_test(xa, na, xb, nb, tails)
    se = math.sqrt(pa * (1 - pa) / na + pb * (1 - pb) / nb)
    zc = N.inv_cdf(1 - alpha / 2)
    diff = pb - pa
    prob_b, exp_loss, exp_lift = bayes(xa, na, xb, nb)
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    exp_counts = [na * (xa + xb) / float(na + nb), nb * (xa + xb) / float(na + nb),
                  na * (na + nb - xa - xb) / float(na + nb), nb * (na + nb - xa - xb) / float(na + nb)]
    res = {"rate_a_pct": round(pa * 100, 3), "rate_b_pct": round(pb * 100, 3),
           "absolute_lift_pct_points": round(diff * 100, 3),
           "relative_lift_pct": round(diff / pa * 100, 2) if pa else None,
           "confidence_interval_diff_pct_points": [round((diff - zc * se) * 100, 3), round((diff + zc * se) * 100, 3)],
           "z_score": round(z, 3), "p_value": round(p, 5), "alpha": alpha, "tails": tails}
    if min(exp_counts) < 5:
        pf = fisher_exact(xa, na, xb, nb)
        res["fisher_exact_p_value"] = round(pf, 5)
        add("info", "SMALL_COUNTS", "Some expected cell counts are < 5, so the z-test is unreliable; use the Fisher exact p-value (%.4f)." % pf)
        p_use = pf
    else:
        p_use = p
    res["significant"] = p_use < alpha
    res["bayesian_prob_b_beats_a"] = round(prob_b, 4)
    res["bayesian_expected_lift_pct"] = round(exp_lift * 100, 2)
    res["bayesian_expected_loss_if_choose_b_pct_points"] = round(exp_loss * 100, 4)
    if res["significant"]:
        winner = "B" if diff > 0 else "A"
        res["verdict"] = "Statistically significant at alpha=%.2f: variant %s is better." % (alpha, winner)
    else:
        res["verdict"] = "Not significant at alpha=%.2f: keep testing or treat as a tie." % alpha
    if min(xa, xb) < 30:
        add("warning", "FEW_CONVERSIONS", "Fewer than 30 conversions in a variant; results are fragile.")
    add("info", "NO_PEEKING", "These p-values assume a fixed sample size. Checking repeatedly and stopping at the first 'significant' result inflates false positives.")
    res["issues"] = sort_issues(issues)
    return res


def sample_size(baseline, mde, alpha=0.05, power=0.8, relative=True):
    p1 = baseline
    p2 = baseline * (1 + mde) if relative else baseline + mde
    if not 0 < p1 < 1 or not 0 < p2 < 1:
        raise ToolError("baseline and target rates must be between 0 and 1 (got %.4f -> %.4f)" % (p1, p2))
    za, zb = N.inv_cdf(1 - alpha / 2), N.inv_cdf(power)
    pbar = (p1 + p2) / 2.0
    n = ((za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2) / ((p2 - p1) ** 2)
    return int(math.ceil(n)), p1, p2


def _args(p):
    p.add_argument("--a-visitors", type=int, required=True, help="visitors/clicks/impressions in control A")
    p.add_argument("--a-conversions", type=int, required=True)
    p.add_argument("--b-visitors", type=int, required=True)
    p.add_argument("--b-conversions", type=int, required=True)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--one-tailed", action="store_true", help="test only 'B better than A' (use only if decided in advance)")


def _run(a):
    return analyze(a.a_conversions, a.a_visitors, a.b_conversions, a.b_visitors, a.alpha, 1 if a.one_tailed else 2)


def _ss_args(p):
    p.add_argument("--baseline", required=True, help="current conversion rate / CTR, e.g. 3%% or 0.03")
    p.add_argument("--mde", required=True, help="smallest lift worth detecting, e.g. 20%% (relative) or 0.5 with --absolute")
    p.add_argument("--absolute", action="store_true", help="treat --mde as percentage points (0.5 = +0.5 pts)")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p.add_argument("--variants", type=int, default=2, help="total variants including control")
    p.add_argument("--daily-traffic", type=float, help="visitors per day entering the test (all variants combined)")


def _ss_run(a):
    base = parse_ratio(a.baseline)
    if a.absolute:
        mde = float(a.mde.strip("%")) / 100.0
    else:
        mde = parse_ratio(a.mde) if str(a.mde).strip().endswith("%") or float(a.mde) > 1 else float(a.mde)
    n, p1, p2 = sample_size(base, mde, a.alpha, a.power, relative=not a.absolute)
    total = n * a.variants
    res = {"baseline_rate_pct": round(p1 * 100, 3), "target_rate_pct": round(p2 * 100, 3), "visitors_per_variant": n,
           "total_visitors": total, "alpha": a.alpha, "power": a.power,
           "expected_conversions_per_variant_at_baseline": int(n * p1)}
    if a.daily_traffic:
        res["estimated_days"] = round(total / a.daily_traffic, 1)
        if total / a.daily_traffic < 7:
            res["advice"] = "Run for at least 7 full days (ideally 14) to cover weekday/weekend cycles."
    return res


TOOLS = [
    Tool("abtest", "Ads / Math & testing", "A/B significance: z-test, Fisher exact, confidence interval, Bayesian P(B>A)", _args, _run),
    Tool("samplesize", "Ads / Math & testing", "Sample size and test duration for a CTR/CVR experiment (alpha, power, MDE)", _ss_args, _ss_run),
]
