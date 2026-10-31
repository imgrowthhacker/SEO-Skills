"""Collects TOOLS from every module. Add a module here to register new tools."""
from __future__ import annotations

import importlib

MODULES = [
    "seo.onpage", "seo.serp", "seo.metagen", "seo.slug", "seo.schema",
    "seo.robots", "seo.sitemap", "seo.crawler", "seo.redirects", "seo.headers",
    "seo.hreflang", "seo.pagespeed", "seo.linkgraph", "seo.audit",
    "seo.keywords", "seo.readability", "seo.duplicates",
    "seo.gsc", "seo.logs",
    "ads.adcopy", "ads.adtags", "ads.utm", "ads.adstxt",
    "ads.metrics", "ads.abtest", "ads.budget",
    "ads.adkeywords", "ads.searchterms",
]


def all_tools():
    tools = []
    for m in MODULES:
        mod = importlib.import_module("seo_ads_toolkit." + m)
        tools.extend(mod.TOOLS)
    names = [t.name for t in tools]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise RuntimeError("duplicate tool names: %s" % ", ".join(sorted(dupes)))
    return tools
