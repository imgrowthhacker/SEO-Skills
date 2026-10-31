"""Meta tag / Open Graph / Twitter Card / hreflang generator."""
from __future__ import annotations

import html

from ..core import Tool, ToolError
from .serp import analyze_snippet


def generate(title, description, url=None, image=None, site_name=None, og_type="website",
             locale=None, twitter_site=None, robots=None, alternates=None, lang=None, theme_color=None):
    e = lambda s: html.escape(s, quote=True)
    L = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
         "<title>%s</title>" % html.escape(title), '<meta name="description" content="%s">' % e(description)]
    if robots:
        L.append('<meta name="robots" content="%s">' % e(robots))
    if url:
        L.append('<link rel="canonical" href="%s">' % e(url))
    for code, href in (alternates or {}).items():
        L.append('<link rel="alternate" hreflang="%s" href="%s">' % (e(code), e(href)))
    if theme_color:
        L.append('<meta name="theme-color" content="%s">' % e(theme_color))
    L.append("")
    L += ['<meta property="og:title" content="%s">' % e(title),
          '<meta property="og:description" content="%s">' % e(description),
          '<meta property="og:type" content="%s">' % e(og_type)]
    if url:
        L.append('<meta property="og:url" content="%s">' % e(url))
    if image:
        L.append('<meta property="og:image" content="%s">' % e(image))
    if site_name:
        L.append('<meta property="og:site_name" content="%s">' % e(site_name))
    if locale:
        L.append('<meta property="og:locale" content="%s">' % e(locale))
    L.append("")
    L += ['<meta name="twitter:card" content="%s">' % ("summary_large_image" if image else "summary"),
          '<meta name="twitter:title" content="%s">' % e(title),
          '<meta name="twitter:description" content="%s">' % e(description)]
    if image:
        L.append('<meta name="twitter:image" content="%s">' % e(image))
    if twitter_site:
        L.append('<meta name="twitter:site" content="%s">' % e(twitter_site if twitter_site.startswith("@") else "@" + twitter_site))
    return "\n".join(L)


def _args(p):
    p.add_argument("--title", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--url", help="canonical / og:url")
    p.add_argument("--image", help="absolute URL of share image (1200x630 recommended)")
    p.add_argument("--site-name")
    p.add_argument("--type", default="website", dest="og_type", help="og:type (website, article, product...)")
    p.add_argument("--locale", help="e.g. en_US")
    p.add_argument("--twitter-site", help="@handle")
    p.add_argument("--robots", help="e.g. 'index,follow' or 'noindex,nofollow'")
    p.add_argument("--theme-color")
    p.add_argument("--alt", action="append", default=[], metavar="LANG=URL", help="hreflang alternate, repeatable (e.g. es=https://x.com/es/)")


def _run(a):
    alts = {}
    for item in a.alt:
        if "=" not in item:
            raise ToolError("--alt must look like es=https://example.com/es/")
        k, v = item.split("=", 1)
        alts[k.strip()] = v.strip()
    snippet = generate(a.title, a.description, a.url, a.image, a.site_name, a.og_type, a.locale,
                       a.twitter_site, a.robots, alts, theme_color=a.theme_color)
    check = analyze_snippet(a.title, a.description, a.url or "")
    warn = "".join("<!-- %s: %s -->\n" % (i["severity"], i["message"]) for i in check["issues"])
    return {"_raw": warn + snippet, "html": snippet, "issues": check["issues"]}


TOOLS = [Tool("metagen", "SEO / On-page", "Generate title, meta description, canonical, Open Graph, Twitter Card, hreflang tags", _args, _run)]
