# SEO & Ads Toolkit

38 open-source SEO and paid-ads tools in one command-line app. **Pure Python standard library: no dependencies, no API keys.** Works on Python 3.8+.

```bash
python -m seo_ads_toolkit list              # see every tool
python -m seo_ads_toolkit <tool> --help     # options for one tool
# or: pip install .   ->   seo-ads <tool> ...
```

Every tool supports `--json`, `--out file.(json|csv|txt)`, and (where it finds issues) `--fail-on error|warning` so it can gate CI.
Network tools accept `--timeout` and `--ua`. The crawler obeys robots.txt by default.

## Tools

### SEO - On-page
| Tool | What it does |
|---|---|
| `onpage URL [-k keyword]` | Title, meta, H1-H6, canonical, images, links, OG, keyword placement, score |
| `serp` | Google snippet preview with pixel-width truncation check (+ HTML preview) |
| `metagen` | Generate title/description/canonical/Open Graph/Twitter/hreflang tags |
| `slug` | Clean URL slugs or URL quality checks |
| `schema` / `schema-gen` | Validate JSON-LD (required/recommended fields, ISO dates, prices) / generate Product, Article, LocalBusiness, FAQ, Breadcrumb... |
| `audit URL --html report.html` | One-command site audit: crawl + robots + sitemap + headers + redirects + on-page, ranked "fix first" list |

### SEO - Technical
`robots` (Google-style matching tester) · `robots-gen` (presets, AI-bot blocking) · `sitemap` (validate, HTTP-check) · `sitemap-gen` (from list or crawl, auto-split) · `crawl` · `links` (broken-link checker) · `redirects` (chains, loops, www/https variants) · `headers` (X-Robots-Tag, compression, TTFB, security) · `hreflang` (with reciprocity check) · `pagespeed` (static check + Google PageSpeed Insights API) · `linkgraph` (internal PageRank, weak/orphan pages)

### SEO - Content & keywords
`keywords` (n-grams, density) · `suggest` (autocomplete ideas with intent) · `readability` (Flesch, Fog, SMOG...) · `duplicates` (near-duplicate detection)

### SEO - Data & reporting
`gsc` (Search Console CSV: striking-distance, CTR gaps, cannibalization, brand split, period compare) · `logs` (bot crawl analysis, crawl waste, fake-bot DNS check)

### Ads
`adcopy` (RSA/Meta/LinkedIn/X/TikTok limits + policy flags) · `tags` (GA4/GTM/Meta/TikTok/etc. detection) · `landing` (landing-page checklist) · `utm` (build/parse/check/strip/bulk) · `adstxt` · `calc` (CTR/CPC/CPA/ROAS/break-even) · `bid` · `abtest` (z-test, Fisher, Bayesian) · `samplesize` · `budget` (pacing + forecast) · `permute` · `negatives` · `cluster` · `searchterms` (wasted spend, negative candidates)

## Examples

```bash
seo-ads audit https://example.com --max-pages 200 --html report.html
seo-ads onpage https://example.com/page -k "blue widgets"
seo-ads robots https://example.com --test /cart --ua Googlebot
seo-ads schema-gen Product --set name=Widget --set offers.price=9.99 --set offers.priceCurrency=USD
seo-ads gsc queries.csv --brand acme --min-impressions 200
seo-ads logs access.log.gz --bot Googlebot --urls important.txt
seo-ads calc -i 10000 -c 300 --cost 450 -v 15 -r 1500 --margin 40%
seo-ads abtest --a-visitors 1000 --a-conversions 50 --b-visitors 1000 --b-conversions 80
seo-ads utm build https://x.com/p -s google -m cpc -c spring_sale
seo-ads adcopy --file examples/ads_copy.json -k "blue widgets"
seo-ads searchterms search_terms.csv --min-clicks 10
```
Sample input files are in `examples/`.

## Limits and honesty notes
- Tools read **static HTML**; content rendered only by JavaScript is not seen (the audit says so where it matters).
- Ad platform limits, schema requirements and Core Web Vitals thresholds are encoded from published specs and **change over time**; confirm in the platform before acting.
- Scores (audit, on-page, landing, internal PageRank) are triage aids, not ranking predictions.
- `suggest` uses Google's unofficial autocomplete endpoint; `pagespeed --psi` uses Google's public API (add `--key` for quota). Both need internet.
- Only crawl sites you own or have permission to test; use `--delay` and keep `--workers` low.

## Develop
```bash
python -m unittest discover -s tests -t .
```
Add a tool: create a module exposing `TOOLS = [Tool(name, category, help, add_args, run)]` and list it in `seo_ads_toolkit/registry.py`.

MIT licensed.
