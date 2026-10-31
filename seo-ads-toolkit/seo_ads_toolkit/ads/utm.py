"""UTM builder, parser, validator, cleaner and CSV bulk builder."""
from __future__ import annotations

import csv
import re
import urllib.parse

from ..core import Tool, ToolError, issue, read_table, sort_issues, write_csv

UTM = ["source", "medium", "campaign", "term", "content", "id"]
CLICK_IDS = ["gclid", "gbraid", "wbraid", "fbclid", "msclkid", "ttclid", "li_fat_id", "twclid", "dclid", "yclid", "mc_eid", "_ga", "_gl"]
KNOWN_MEDIUMS = {"cpc", "ppc", "paidsearch", "paid-search", "paid_search", "display", "cpm", "banner", "email", "affiliate", "referral",
                 "social", "paid-social", "paid_social", "paidsocial", "social-network", "organic", "video", "sms", "push", "audio", "qr", "print", "podcast"}


def clean_value(v, lower=True, sep="_"):
    v = v.strip()
    if lower:
        v = v.lower()
    return re.sub(r"[\s]+", sep, v)


def build(url, source, medium, campaign, term=None, content=None, utm_id=None, lower=True, sep="_"):
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not k.startswith("utm_")]
    vals = {"source": source, "medium": medium, "campaign": campaign, "term": term, "content": content, "id": utm_id}
    for k in UTM:
        if vals[k]:
            q.append(("utm_" + k, clean_value(vals[k], lower, sep)))
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q, quote_via=urllib.parse.quote), p.fragment))


def parse(url):
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    return {"base_url": urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", "")),
            "utm": {k: v for k, v in q if k.startswith("utm_")},
            "click_ids": {k: v[:20] for k, v in q if k.lower() in CLICK_IDS},
            "other_params": {k: v for k, v in q if not k.startswith("utm_") and k.lower() not in CLICK_IDS},
            "fragment": p.fragment}


def strip(url, keep=()):
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
         if k in keep or not (k.startswith("utm_") or k.lower() in CLICK_IDS)]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q, quote_via=urllib.parse.quote), p.fragment))


def check(url):
    info = parse(url)
    u = info["utm"]
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    for k in ("source", "medium", "campaign"):
        if "utm_" + k not in u:
            add("error", "MISSING_" + k.upper(), "utm_%s is missing (GA4 needs source, medium and campaign for proper attribution)." % k)
    if "utm_term" in u and "utm_source" in u and "{" in u["utm_term"]:
        add("info", "VALUE_TEMPLATE", "utm_term contains a {template}; make sure the platform fills it in.")
    for k, v in u.items():
        if v != v.lower():
            add("warning", "UPPERCASE", "%s=%r: GA4 treats 'Email' and 'email' as different values; use lowercase." % (k, v))
        if " " in v or "%20" in v or "+" in v:
            add("warning", "SPACES", "%s=%r contains spaces; use '_' or '-'." % (k, v))
        if not v:
            add("error", "EMPTY_" + k.upper(), "%s is empty." % k)
    m = u.get("utm_medium", "").lower()
    if m and m not in KNOWN_MEDIUMS:
        add("info", "UNKNOWN_MEDIUM", "utm_medium=%r is not one GA4's default channel groups recognise; traffic may land in 'Unassigned'/'Referral'." % m)
    if "utm_source" in u and u["utm_source"].lower() in ("google", "bing", "facebook", "meta", "instagram", "linkedin", "tiktok") and "gclid" in info["click_ids"] and m not in ("cpc", "ppc", "paid-search"):
        add("info", "AUTO_TAGGING", "gclid is present (auto-tagging); manual UTMs on Google Ads can conflict with it unless overrides are intended.")
    for k in info["utm"]:
        if k not in ["utm_" + x for x in UTM] + ["utm_source_platform", "utm_creative_format", "utm_marketing_tactic"]:
            add("warning", "UNKNOWN_PARAM", "%s is not a standard UTM parameter." % k)
    if info["fragment"] and "?" in info["fragment"]:
        add("warning", "PARAMS_IN_FRAGMENT", "Query parameters after '#' are not sent to the server and may not be read by analytics.")
    return {"url": url, "parsed": info, "issues": sort_issues(issues)}


def _args(p):
    p.add_argument("action", choices=["build", "parse", "check", "strip", "bulk"])
    p.add_argument("url", nargs="?", help="URL (for bulk: input CSV path with columns url,source,medium,campaign[,term,content,id])")
    p.add_argument("--source", "-s")
    p.add_argument("--medium", "-m")
    p.add_argument("--campaign", "-c")
    p.add_argument("--term", "-t")
    p.add_argument("--content")
    p.add_argument("--id", dest="utm_id")
    p.add_argument("--keep-case", action="store_true", help="do not lowercase values")
    p.add_argument("--sep", default="_", help="replacement for spaces (default _)")
    p.add_argument("--keep", action="append", default=[], help="(strip) parameter names to keep")


def _run(a):
    if not a.url:
        raise ToolError("a URL (or CSV path for bulk) is required")
    lower = not a.keep_case
    if a.action == "build":
        miss = [k for k in ("source", "medium", "campaign") if not getattr(a, k)]
        if miss:
            raise ToolError("build needs --source, --medium and --campaign (missing: %s)" % ", ".join(miss))
        out = build(a.url, a.source, a.medium, a.campaign, a.term, a.content, a.utm_id, lower, a.sep)
        res = check(out)
        return {"_raw": out, "url": out, "issues": res["issues"]}
    if a.action == "parse":
        return parse(a.url)
    if a.action == "check":
        return check(a.url)
    if a.action == "strip":
        return {"_raw": strip(a.url, a.keep), "url": strip(a.url, a.keep)}
    rows = read_table(a.url)
    if len(rows) < 2:
        raise ToolError("CSV needs a header and at least one row")
    hdr = [h.strip().lower().replace("utm_", "") for h in rows[0]]
    for need in ("url", "source", "medium", "campaign"):
        if need not in hdr:
            raise ToolError("CSV is missing required column %r (found: %s)" % (need, ", ".join(hdr)))
    out_rows, issues = [], []
    for n, r in enumerate(rows[1:], 2):
        if not any(c.strip() for c in r):
            continue
        d = {h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(hdr)}
        try:
            if not (d["url"] and d["source"] and d["medium"] and d["campaign"]):
                raise ToolError("row %d: url, source, medium and campaign are required" % n)
            built = build(d["url"], d["source"], d["medium"], d["campaign"], d.get("term"), d.get("content"), d.get("id"), lower, a.sep)
            out_rows.append(dict(d, tagged_url=built))
            issues += [dict(i, message="row %d: %s" % (n, i["message"])) for i in check(built)["issues"] if i["severity"] != "info"]
        except ToolError as e:
            issues.append(issue("error", "BAD_ROW", str(e)))
    return {"built": len(out_rows), "rows": out_rows, "issues": sort_issues(issues)}


TOOLS = [Tool("utm", "Ads / Copy, tracking & landing pages", "UTM builder/parser/checker/stripper + bulk CSV (preserves params & #fragments)", _args, _run)]
