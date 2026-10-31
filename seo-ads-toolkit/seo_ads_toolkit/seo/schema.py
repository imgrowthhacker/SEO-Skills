"""Structured data (JSON-LD) extractor, validator and generator.

Required/recommended properties follow Google's structured-data documentation
at the time of writing. Always confirm with Google's Rich Results Test, since
requirements change.
"""
from __future__ import annotations

import json
import re

from ..core import Tool, ToolError, add_net_args, issue, load_page, read_text_file, sort_issues

# type -> (required, recommended)
RULES = {
    "Article": ([], ["headline", "image", "datePublished", "dateModified", "author"]),
    "NewsArticle": ([], ["headline", "image", "datePublished", "dateModified", "author"]),
    "BlogPosting": ([], ["headline", "image", "datePublished", "dateModified", "author"]),
    "Product": (["name"], ["image", "description", "sku", "brand", "offers", "aggregateRating", "review"]),
    "Offer": (["price", "priceCurrency"], ["availability", "url", "priceValidUntil"]),
    "FAQPage": (["mainEntity"], []),
    "Question": (["name", "acceptedAnswer"], []),
    "Answer": (["text"], []),
    "BreadcrumbList": (["itemListElement"], []),
    "ListItem": (["position", "name"], ["item"]),
    "Organization": ([], ["name", "url", "logo", "sameAs", "contactPoint"]),
    "LocalBusiness": (["name", "address"], ["telephone", "url", "openingHoursSpecification", "geo", "priceRange", "image"]),
    "Recipe": (["name", "image"], ["author", "datePublished", "description", "recipeIngredient", "recipeInstructions", "aggregateRating"]),
    "Event": (["name", "startDate", "location"], ["description", "endDate", "image", "offers", "eventStatus"]),
    "VideoObject": (["name", "thumbnailUrl", "uploadDate"], ["description", "contentUrl", "embedUrl", "duration"]),
    "JobPosting": (["title", "description", "datePosted", "hiringOrganization", "jobLocation"], ["validThrough", "employmentType", "baseSalary"]),
    "WebSite": ([], ["name", "url"]),
    "Person": (["name"], ["url", "sameAs"]),
    "AggregateRating": (["ratingValue"], ["reviewCount", "ratingCount", "bestRating"]),
    "Review": (["author", "reviewRating"], ["itemReviewed", "datePublished"]),
    "PostalAddress": ([], ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]),
}
SUBTYPES = {"LocalBusiness": ["Restaurant", "Store", "Dentist", "Physician", "LegalService", "RealEstateAgent", "AutoRepair",
                              "HealthAndBeautyBusiness", "FoodEstablishment", "ProfessionalService", "HomeAndConstructionBusiness"]}
for _parent, _subs in SUBTYPES.items():
    for _s in _subs:
        RULES.setdefault(_s, RULES[_parent])
DATE_FIELDS = {"datePublished", "dateModified", "startDate", "endDate", "uploadDate", "datePosted", "validThrough", "priceValidUntil"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
NESTED_TYPE = {"address": "PostalAddress", "offers": "Offer", "author": "Person", "publisher": "Organization",
               "geo": "GeoCoordinates", "brand": "Brand", "aggregateRating": "AggregateRating",
               "location": "Place", "hiringOrganization": "Organization", "acceptedAnswer": "Answer",
               "reviewRating": "Rating", "contactPoint": "ContactPoint"}
NUMERIC = {"latitude", "longitude", "ratingValue", "reviewCount", "ratingCount", "bestRating", "worstRating", "position"}


def _types(node):
    t = node.get("@type")
    return [t] if isinstance(t, str) else [x for x in (t or []) if isinstance(x, str)]


def _walk(obj, path="$"):
    """Yield (path, node) for every dict that has @type."""
    if isinstance(obj, dict):
        if "@type" in obj:
            yield path, obj
        for k, v in obj.items():
            if k.startswith("@") and k != "@graph":
                continue
            for item in _walk(v, "%s.%s" % (path, k)):
                yield item
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for item in _walk(v, "%s[%d]" % (path, i)):
                yield item


def _present(node, prop):
    v = node.get(prop)
    return v not in (None, "", [], {})


def validate_blocks(blocks):
    """blocks: list of raw JSON-LD strings. Returns dict with nodes + issues."""
    issues, nodes = [], []
    for n, raw in enumerate(blocks, 1):
        try:
            data = json.loads(raw)
        except ValueError as e:
            issues.append(issue("error", "JSONLD_INVALID", "Block %d is not valid JSON: %s" % (n, e)))
            continue
        top = data if isinstance(data, list) else [data]
        for t in top:
            if isinstance(t, dict):
                ctx = json.dumps(t.get("@context", ""))
                if "@graph" not in t and "schema.org" not in ctx:
                    issues.append(issue("warning", "CONTEXT_MISSING", "Block %d: @context should be https://schema.org." % n))
        for path, node in _walk(data):
            for typ in _types(node):
                req, rec = RULES.get(typ, ([], []))
                miss_req = [p for p in req if not _present(node, p)]
                miss_rec = [p for p in rec if not _present(node, p)]
                nodes.append({"block": n, "path": path, "type": typ, "missing_required": ",".join(miss_req) or None,
                              "missing_recommended": ",".join(miss_rec) or None})
                if miss_req:
                    issues.append(issue("error", "MISSING_REQUIRED", "%s at %s lacks required: %s" % (typ, path, ", ".join(miss_req))))
                if miss_rec:
                    issues.append(issue("info", "MISSING_RECOMMENDED", "%s at %s lacks recommended: %s" % (typ, path, ", ".join(miss_rec))))
                for f in DATE_FIELDS:
                    v = node.get(f)
                    if isinstance(v, str) and not _ISO.match(v.strip()):
                        issues.append(issue("warning", "DATE_FORMAT", "%s.%s=%r is not ISO 8601." % (typ, f, v)))
                if typ == "Offer" and isinstance(node.get("price"), str) and not re.fullmatch(r"\d+(\.\d+)?", node["price"].strip()):
                    issues.append(issue("warning", "PRICE_FORMAT", "Offer.price=%r should be a plain number." % node["price"]))
                if typ == "FAQPage":
                    me = node.get("mainEntity")
                    if isinstance(me, list) and len(me) == 0:
                        issues.append(issue("error", "FAQ_EMPTY", "FAQPage has an empty mainEntity."))
                if typ == "BreadcrumbList" and isinstance(node.get("itemListElement"), list):
                    pos = [i.get("position") for i in node["itemListElement"] if isinstance(i, dict)]
                    if pos and [int(p) for p in pos if str(p).isdigit()] != list(range(1, len(pos) + 1)):
                        issues.append(issue("warning", "BREADCRUMB_ORDER", "Breadcrumb positions should run 1..n in order."))
    return {"nodes": nodes, "issues": sort_issues(issues)}


def _args(p):
    p.add_argument("target", help="URL, local .html file, or .json/.jsonld file")
    add_net_args(p)


def _run(a):
    if a.target.lower().endswith((".json", ".jsonld")):
        blocks, micro = [read_text_file(a.target)], []
    else:
        page, _ = load_page(a.target, a.ua, a.timeout)
        blocks, micro = page.jsonld, page.microdata_types
    res = validate_blocks(blocks)
    if not blocks and not micro:
        res["issues"].insert(0, issue("warning", "NO_STRUCTURED_DATA", "No JSON-LD or microdata found."))
    res["jsonld_blocks"] = len(blocks)
    res["microdata_types"] = micro
    return res


# ------------------------------------------------------------------ generator
def _coerce(k, v):
    if k in NUMERIC:
        try:
            f = float(v)
            return int(f) if f == int(f) else f
        except ValueError:
            pass
    return v


def build_from_sets(typ, sets):
    obj = {"@context": "https://schema.org", "@type": typ}
    for item in sets:
        if "=" not in item:
            raise ToolError("--set must look like key=value, got %r" % item)
        key, val = item.split("=", 1)
        parts = key.strip().split(".")
        cur = obj
        for i, part in enumerate(parts[:-1]):
            nxt = cur.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                if part in NESTED_TYPE:
                    nxt["@type"] = NESTED_TYPE[part]
                cur[part] = nxt
            cur = nxt
        leaf = parts[-1]
        val = _coerce(leaf, val.strip())
        if leaf in cur:
            if not isinstance(cur[leaf], list):
                cur[leaf] = [cur[leaf]]
            cur[leaf].append(val)
        else:
            cur[leaf] = val
    return obj


def build_faq(pairs):
    return {"@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in pairs]}


def build_breadcrumb(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [dict({"@type": "ListItem", "position": i, "name": n}, **({"item": u} if u else {}))
                                for i, (n, u) in enumerate(items, 1)]}


def parse_faq_file(path):
    text = read_text_file(path)
    pairs, q, a = [], None, []
    for line in text.splitlines():
        if re.match(r"^\s*Q\s*:", line, re.I):
            if q and a:
                pairs.append((q, " ".join(a).strip()))
            q, a = re.sub(r"^\s*Q\s*:\s*", "", line, flags=re.I).strip(), []
        elif re.match(r"^\s*A\s*:", line, re.I):
            a = [re.sub(r"^\s*A\s*:\s*", "", line, flags=re.I).strip()]
        elif q and line.strip():
            a.append(line.strip())
    if q and a:
        pairs.append((q, " ".join(a).strip()))
    if not pairs:
        raise ToolError("no Q:/A: pairs found in %s" % path)
    return pairs


def _gen_args(p):
    p.add_argument("type", help="schema.org type (Product, Article, Organization, LocalBusiness, Event, ...) or 'faq' / 'breadcrumb'")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   help="property; dot notation for nesting (offers.price=9.99); repeat a key for lists")
    p.add_argument("--faq-file", help="text file with 'Q: ...' and 'A: ...' lines (for type faq)")
    p.add_argument("--crumb", action="append", default=[], metavar="NAME|URL", help="breadcrumb item (for type breadcrumb), in order")
    p.add_argument("--no-script", action="store_true", help="print bare JSON instead of a <script> tag")


def _gen_run(a):
    t = a.type
    if t.lower() == "faq":
        if not a.faq_file:
            raise ToolError("faq needs --faq-file with Q:/A: lines")
        obj = build_faq(parse_faq_file(a.faq_file))
    elif t.lower() == "breadcrumb":
        if not a.crumb:
            raise ToolError("breadcrumb needs one or more --crumb 'Name|https://url'")
        obj = build_breadcrumb([(c.split("|", 1) + [""])[:2] for c in a.crumb])
    else:
        obj = build_from_sets(t, a.set)
    js = json.dumps(obj, indent=2, ensure_ascii=False)
    check = validate_blocks([js])
    out = js if a.no_script else '<script type="application/ld+json">\n%s\n</script>' % js
    return {"_raw": out, "jsonld": obj, "issues": check["issues"]}


TOOLS = [
    Tool("schema", "SEO / On-page", "Extract and validate JSON-LD structured data (required/recommended fields, dates, prices)", _args, _run),
    Tool("schema-gen", "SEO / On-page", "Generate JSON-LD (Product, Article, LocalBusiness, FAQ, Breadcrumb, Event...)", _gen_args, _gen_run),
]
