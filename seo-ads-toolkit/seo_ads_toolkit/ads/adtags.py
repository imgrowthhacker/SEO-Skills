"""Tracking-tag detector and ads landing-page checker."""
from __future__ import annotations

import re

from ..core import Tool, ToolError, add_net_args, fetch, issue, load_page, parse_html, sort_issues, tokenize

# name -> list of (regex, id_group or None)
TAGS = {
    "Google Tag Manager": [(r"GTM-[A-Z0-9]{4,10}", 0)],
    "Google Analytics 4": [(r"googletagmanager\.com/gtag/js\?id=(G-[A-Z0-9]{6,12})", 1), (r"gtag\(\s*['\"]config['\"]\s*,\s*['\"](G-[A-Z0-9]{6,12})", 1)],
    "Universal Analytics (obsolete)": [(r"\bUA-\d{4,10}-\d{1,3}\b", 0), (r"google-analytics\.com/analytics\.js", None)],
    "Google Ads (gtag)": [(r"['\"](AW-\d{6,12})(?:/[\w-]+)?['\"]", 1), (r"googleadservices\.com/pagead/conversion", None)],
    "Meta (Facebook) Pixel": [(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"]?(\d{8,18})", 1), (r"connect\.facebook\.net/[\w_]+/fbevents\.js", None)],
    "TikTok Pixel": [(r"ttq\.load\(\s*['\"]([A-Z0-9]{8,30})", 1), (r"analytics\.tiktok\.com/i18n/pixel", None)],
    "LinkedIn Insight": [(r"_linkedin_partner_id\s*=\s*['\"]?(\d+)", 1), (r"snap\.licdn\.com/li\.lms-analytics", None)],
    "Pinterest Tag": [(r"pintrk\(\s*['\"]load['\"]\s*,\s*['\"]?(\d+)", 1), (r"s\.pinimg\.com/ct/core\.js", None)],
    "Microsoft Ads UET": [(r"bat\.bing\.com/bat\.js", None), (r"\bti\s*:\s*['\"]?(\d{6,10})", 1)],
    "X (Twitter) Pixel": [(r"twq\(\s*['\"]config['\"]\s*,\s*['\"]([\w]+)", 1), (r"static\.ads-twitter\.com/uwt\.js", None)],
    "Snap Pixel": [(r"snaptr\(\s*['\"]init['\"]\s*,\s*['\"]([\w-]+)", 1), (r"sc-static\.net/scevent", None)],
    "Reddit Pixel": [(r"rdt\(\s*['\"]init['\"]\s*,\s*['\"]([\w-]+)", 1), (r"redditstatic\.com/ads/pixel\.js", None)],
    "Hotjar": [(r"hjid\s*[:=]\s*(\d+)", 1), (r"static\.hotjar\.com", None)],
    "Microsoft Clarity": [(r"clarity\.ms/tag/([a-z0-9]+)", 1)],
    "HubSpot": [(r"js\.hs-scripts\.com/(\d+)\.js", 1)],
    "Segment": [(r"cdn\.segment\.com/analytics\.js/v1/([\w]+)/", 1)],
    "Matomo": [(r"matomo\.js|piwik\.js", None)],
    "Plausible": [(r"plausible\.io/js/", None)],
}
CMP = {"OneTrust": r"cdn\.cookielaw\.org|onetrust", "Cookiebot": r"consent\.cookiebot\.com", "Usercentrics": r"usercentrics",
       "TrustArc": r"consent\.trustarc\.com", "Didomi": r"sdk\.privacy-center\.org|didomi", "Cookie Information": r"cookieinformation"}
CTA_WORDS = re.compile(r"\b(buy|get|start|sign ?up|register|book|call|request|contact|quote|free trial|try|download|subscribe|add to cart|order|shop|claim|apply|schedule|demo)\b", re.I)
TRUST_WORDS = re.compile(r"\b(testimonials?|reviews?|rated|guarantee|money[- ]back|trusted by|secure|certified|award|years of experience|customers)\b", re.I)


def detect_tags(html):
    found = []
    for name, pats in TAGS.items():
        ids, hit = set(), False
        for rx, grp in pats:
            for m in re.finditer(rx, html, re.I if grp is None else 0):
                hit = True
                if grp is not None:
                    ids.add(m.group(grp))
        if hit:
            found.append({"tag": name, "ids": ", ".join(sorted(ids)) or "-"})
    cmp = [n for n, rx in CMP.items() if re.search(rx, html, re.I)]
    consent_mode = bool(re.search(r"gtag\(\s*['\"]consent['\"]\s*,\s*['\"]default['\"]", html))
    cm_v2 = bool(re.search(r"ad_user_data", html) and re.search(r"ad_personalization", html))
    return found, cmp, consent_mode, cm_v2


def analyze_tags(html):
    found, cmp, cm, cm2 = detect_tags(html)
    names = {f["tag"] for f in found}
    issues = []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    gtm = next((f for f in found if f["tag"] == "Google Tag Manager"), None)
    if "Universal Analytics (obsolete)" in names:
        add("warning", "UA_OBSOLETE", "Universal Analytics (UA-) code found; it stopped processing data in 2023-2024. Migrate to GA4.")
    if gtm and len(gtm["ids"].split(", ")) > 1:
        add("info", "MULTIPLE_GTM", "Several GTM containers: %s" % gtm["ids"])
    if gtm and ({"Google Analytics 4", "Meta (Facebook) Pixel"} & names):
        add("info", "TAGS_BOTH_PLACES", "GTM plus hard-coded GA4/Meta tags: make sure each event fires once (check for double counting).")
    ga4 = next((f for f in found if f["tag"] == "Google Analytics 4"), None)
    if ga4 and len(ga4["ids"].split(", ")) > 1:
        add("info", "MULTIPLE_GA4", "More than one GA4 measurement ID: %s" % ga4["ids"])
    ad_tags = names & {"Google Ads (gtag)", "Meta (Facebook) Pixel", "TikTok Pixel", "LinkedIn Insight", "Microsoft Ads UET", "Pinterest Tag", "X (Twitter) Pixel", "Snap Pixel", "Reddit Pixel"}
    if ad_tags and not cmp and not cm:
        add("warning", "NO_CONSENT_TOOLING", "Advertising tags found but no consent platform or Consent Mode default detected. EEA/UK/other regions may require prior consent.")
    if cm and not cm2:
        add("info", "CONSENT_MODE_V2", "Consent Mode default found without ad_user_data/ad_personalization (Consent Mode v2 signals).")
    if not found:
        add("warning", "NO_TAGS_DETECTED", "No tracking tags found in the static HTML. Tags injected only by JavaScript/GTM triggers can't be seen without running a browser.")
    return {"tags": found, "consent_platforms": cmp, "consent_mode_default": cm, "consent_mode_v2_signals": cm2,
            "note": "Static detection only: tags that GTM injects after load will not appear.", "issues": sort_issues(issues)}


def _tags_args(p):
    p.add_argument("target", help="URL or local .html file")
    add_net_args(p)


def _tags_run(a):
    import os
    if os.path.isfile(a.target):
        from ..core import read_text_file
        html = read_text_file(a.target)
    else:
        r = fetch(a.target, timeout=a.timeout, ua=a.ua)
        if r.status == 0:
            raise ToolError("could not fetch %s: %s" % (a.target, r.error))
        html = r.text
    return analyze_tags(html)


# ----------------------------------------------------------------- landing page
def analyze_landing(page, resp, html, keywords=()):
    issues, checks = [], []
    add = lambda *a, **k: issues.append(issue(*a, **k))
    url = resp.final_url if resp else ""
    if resp:
        if not url.startswith("https://"):
            add("error", "NOT_HTTPS", "Landing page is not https (browser warnings, lower trust, policy risk).")
        if resp.status != 200:
            add("error", "STATUS", "Final status is %d. Ads pointing at errors waste spend." % resp.status)
        if len(resp.chain) > 2:
            add("warning", "REDIRECTS", "%d redirects before the landing page; each adds latency." % (len(resp.chain) - 1))
        if resp.ttfb > 0.8:
            add("warning", "SLOW_TTFB", "Server response %d ms." % (resp.ttfb * 1000))
        if len(resp.body) > 400 * 1024:
            add("warning", "HEAVY_HTML", "HTML is %d KB." % (len(resp.body) // 1024))
    if not page.viewport:
        add("error", "NO_VIEWPORT", "No mobile viewport meta tag; most ad clicks are on mobile.")
    h1 = page.h1s
    if not h1:
        add("error", "NO_H1", "No H1 headline: visitors need to instantly see the offer.")
    if len(h1) > 1:
        add("info", "MULTIPLE_H1", "%d H1s." % len(h1))
    body_words = tokenize(page.main_text)
    first150 = " ".join(body_words[:150])
    if keywords:
        for k in keywords:
            kt = " ".join(tokenize(k))
            in_h1 = any(kt in " ".join(tokenize(h)) for h in h1)
            in_title = kt in " ".join(tokenize(page.title))
            in_top = kt in first150
            checks.append({"keyword": k, "in_h1": in_h1, "in_title": in_title, "in_first_150_words": in_top})
            if not in_h1:
                add("warning", "MESSAGE_MATCH", "Keyword/ad message %r is not in the H1: weak message match lowers conversion and Quality Score." % k)
            elif not in_top:
                add("info", "KEYWORD_NOT_ABOVE_FOLD", "%r not in the first 150 words." % k)
    ctas = [b for b in page.buttons if CTA_WORDS.search(b)]
    cta_links = [l.text for l in page.links if CTA_WORDS.search(l.text or "") and len(l.text) < 40]
    if not (ctas or cta_links or page.forms):
        add("error", "NO_CTA", "No obvious call to action (button, form or action link).")
    if page.forms and page.form_fields > 6:
        add("warning", "LONG_FORM", "Form has %d fields; every extra field typically reduces completion." % page.form_fields)
    if len(page.links) > 40:
        add("info", "MANY_LINKS", "%d links: navigation can distract from the single conversion goal." % len(page.links))
    if not page.tel_links and not page.forms:
        add("info", "NO_CONTACT_PATH", "No form or tel: link found.")
    if not TRUST_WORDS.search(page.main_text):
        add("info", "NO_TRUST_SIGNALS", "No obvious trust signals (reviews, guarantee, customers, certifications).")
    low = [l.text.lower() for l in page.links]
    if not any("privacy" in t for t in low):
        add("warning", "NO_PRIVACY_LINK", "No privacy policy link found; ad platforms require one when collecting data.")
    if page.noindex:
        add("info", "NOINDEX", "Page is noindex (fine for ad-only pages; Google Ads can still use it).")
    if page.word_count < 100 and not page.forms:
        add("warning", "THIN_PAGE", "Only %d words: thin pages can hurt relevance/Quality Score." % page.word_count)
    nodim = [i for i in page.images if not (i["width"] and i["height"])]
    if nodim:
        add("info", "IMG_NO_DIMENSIONS", "%d images without dimensions (layout shift)." % len(nodim))
    tags = analyze_tags(html)
    for i in tags["issues"]:
        if i["code"] in ("NO_TAGS_DETECTED", "NO_CONSENT_TOOLING"):
            issues.append(i)
    issues = sort_issues(issues)
    score = max(0, 100 - 15 * sum(i["severity"] == "error" for i in issues) - 6 * sum(i["severity"] == "warning" for i in issues) - sum(i["severity"] == "info" for i in issues))
    return {"url": url, "score": score, "title": page.title, "h1": h1, "words": page.word_count,
            "ctas": (ctas + cta_links)[:6], "forms": page.forms, "form_fields": page.form_fields,
            "tags_found": [t["tag"] for t in tags["tags"]], "message_match": checks, "issues": issues}


def _land_args(p):
    p.add_argument("url")
    p.add_argument("--keyword", "-k", action="append", default=[], help="keyword or ad headline to check message match against (repeatable)")
    add_net_args(p)


def _land_run(a):
    import os
    page, resp = load_page(a.url, a.ua, a.timeout)
    if os.path.isfile(a.url):
        from ..core import read_text_file
        html = read_text_file(a.url)
    else:
        html = resp.text
    return analyze_landing(page, resp, html, a.keyword)


TOOLS = [
    Tool("tags", "Ads / Copy, tracking & landing pages", "Detect GA4/GTM/Ads/Meta/TikTok/LinkedIn/UET/etc. tags + consent tooling in page HTML", _tags_args, _tags_run),
    Tool("landing", "Ads / Copy, tracking & landing pages", "Ads landing-page checklist: message match, CTA, form length, speed, trust, mobile, tags", _land_args, _land_run),
]
