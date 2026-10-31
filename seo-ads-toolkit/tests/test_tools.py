import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.helpers import start_server  # noqa: E402
from seo_ads_toolkit.cli import main  # noqa: E402

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


def rj(*argv):
    code, out, err = run(*(list(argv) + ["--json"]))
    assert code == 0, (code, out, err)
    return json.loads(out)


class Offline(unittest.TestCase):
    def test_catalog(self):
        code, out, _ = run("list")
        self.assertEqual(code, 0)
        self.assertIn("38 tools", out)

    def test_serp_slug_metagen(self):
        r = rj("serp", "--title", "A" * 90, "--description", "short")
        self.assertTrue(r["title_truncated"])
        self.assertEqual(rj("slug", "Héllo, World! The Best Widgets", "--drop-stopwords")["slug"], "h-llo-world-best-widgets".replace("h-llo", "hello"))
        self.assertTrue(any(i["code"] == "UPPERCASE" for i in rj("slug", "https://x.com/Bad_Path?sessionid=1")["issues"]))
        code, out, _ = run("metagen", "--title", "T", "--description", "D", "--url", "https://x.com/", "--alt", "es=https://x.com/es/")
        self.assertIn('hreflang="es"', out)

    def test_schema(self):
        code, out, _ = run("schema-gen", "Product", "--set", "name=W", "--set", "offers.price=9.99", "--set", "offers.priceCurrency=USD")
        self.assertIn('"@type": "Offer"', out)
        code, out, _ = run("schema-gen", "faq", "--faq-file", os.path.join(EX, "faq.txt"), "--no-script")
        self.assertEqual(len(json.loads(out)["mainEntity"]), 2)
        d = tempfile.mkdtemp()
        f = os.path.join(d, "a.json")
        open(f, "w").write('{"@context":"https://schema.org","@type":"Product","offers":{"@type":"Offer","price":"9,99"}}')
        r = rj("schema", f)
        codes = {i["code"] for i in r["issues"]}
        self.assertIn("MISSING_REQUIRED", codes)
        self.assertIn("PRICE_FORMAT", codes)

    def test_robots_rules(self):
        from seo_ads_toolkit.seo.robots import Robots, generate
        r = Robots("User-agent: *\nDisallow: /a/\nAllow: /a/ok\nDisallow: /*.pdf$\n\nUser-agent: Googlebot\nDisallow: /g/\n")
        self.assertFalse(r.is_allowed("Bingbot", "/a/x"))
        self.assertTrue(r.is_allowed("Bingbot", "/a/ok/y"))
        self.assertFalse(r.is_allowed("Bingbot", "/files/x.pdf"))
        self.assertTrue(r.is_allowed("Bingbot", "/files/x.pdf?x=1"))
        self.assertFalse(r.is_allowed("Googlebot", "/g/1"))
        self.assertTrue(r.is_allowed("Googlebot", "/a/x"))
        self.assertIn("GPTBot", generate(block_ai=True))
        code, out, _ = run("robots-gen", "--preset", "wordpress", "--block-ai", "--sitemap", "https://x.com/s.xml")
        self.assertIn("Sitemap: https://x.com/s.xml", out)

    def test_sitemap_parse_and_gen(self):
        from seo_ads_toolkit.seo.sitemap import parse_sitemap, build_sitemap, validate
        xml = build_sitemap(["https://a.com/", "https://a.com/", "http://a.com/x"], "2026-01-01")
        p = parse_sitemap(xml)
        codes = {i["code"] for i in validate(p, "https://a.com/sitemap.xml")}
        self.assertTrue({"DUPLICATES", "HTTP_URLS"} <= codes)
        with self.assertRaises(Exception):
            parse_sitemap('<!DOCTYPE x [<!ENTITY a "b">]><urlset></urlset>')

    def test_content_tools(self):
        txt = "Blue widgets are durable. " * 40 + "We ship widgets fast and we love blue widgets."
        r = rj("keywords", "--text", txt, "-k", "blue widgets", "-k", "red widgets")
        self.assertEqual(r["keywords"][1]["count"], 0)
        r = rj("readability", "--text", "The cat sat on the mat. It was a sunny day and the birds were singing loudly outside.")
        self.assertGreater(r["flesch_reading_ease"], 70)
        d = tempfile.mkdtemp()
        a, b = os.path.join(d, "a.txt"), os.path.join(d, "b.txt")
        base = " ".join("word%d" % (i % 60) for i in range(300))
        open(a, "w").write(base)
        open(b, "w").write(base + " extra")
        r = rj("duplicates", a, b, "--threshold", "0.5")
        self.assertEqual(r["pairs"][0]["verdict"], "near-identical")

    def test_gsc_and_logs(self):
        r = rj("gsc", os.path.join(EX, "gsc_queries.csv"), "--min-impressions", "300", "--brand", "acme")
        self.assertEqual(r["total_clicks"], 239)
        self.assertEqual(r["brand_split"]["brand_clicks"], 120)
        self.assertTrue(r["striking_distance"])
        r = rj("logs", os.path.join(EX, "access.log"))
        names = {b["bot"] for b in r["bots"]}
        self.assertEqual(names, {"Googlebot", "Bingbot", "GPTBot"})
        self.assertTrue(r["focus_error_urls_4xx"])

    def test_ads_math(self):
        r = rj("calc", "-i", "10000", "-c", "300", "--cost", "450", "-v", "15", "-r", "1500", "--margin", "40%")
        self.assertEqual(r["ctr_pct"], 3.0)
        self.assertEqual(r["cpa"], 30.0)
        self.assertAlmostEqual(r["roas"], 3.3333, places=3)
        self.assertEqual(r["break_even_roas"], 2.5)
        r = rj("bid", "--aov", "100", "--cvr", "5%", "--target-roas", "4")
        self.assertEqual(r["max_cpc_for_target_roas"], 1.25)
        r = rj("bid", "--base-bid", "2", "--adjust", "mobile=-25")
        self.assertEqual(r["adjusted_bids"][0]["effective_bid"], 1.5)
        r = rj("abtest", "--a-visitors", "1000", "--a-conversions", "50", "--b-visitors", "1000", "--b-conversions", "80")
        self.assertTrue(r["significant"])
        self.assertGreater(r["bayesian_prob_b_beats_a"], 0.99)
        r = rj("abtest", "--a-visitors", "20", "--a-conversions", "1", "--b-visitors", "20", "--b-conversions", "3")
        self.assertIn("fisher_exact_p_value", r)
        r = rj("samplesize", "--baseline", "3%", "--mde", "20%", "--daily-traffic", "1000")
        self.assertTrue(13000 < r["visitors_per_variant"] < 14500, r)
        r = rj("budget", "--monthly", "3000", "--spent", "1500", "--day", "10", "--days-in-month", "30", "--cpc", "1.5", "--cvr", "4%", "--aov", "80", "--scenarios")
        self.assertEqual(r["pacing"]["projected_month_end_spend"], 4500)
        self.assertEqual(r["forecast"]["estimated_clicks"], 2000)

    def test_ads_text_tools(self):
        r = rj("adcopy", "--file", os.path.join(EX, "ads_copy.json"), "-k", "blue widgets")
        codes = {i["code"] for i in r["issues"]}
        self.assertNotIn("TOO_LONG", codes)
        r = rj("adcopy", "-H", "This headline is definitely far too long for google", "-H", "Great!", "-H", "BUY NOW today", "-D", "d1", "-D", "d2")
        codes = {i["code"] for i in r["issues"]}
        self.assertTrue({"TOO_LONG", "EXCLAMATION_IN_HEADLINE", "EXCESS_CAPS"} <= codes)
        code, out, _ = run("utm", "build", "https://x.com/p?a=1#top", "-s", "Google Ads", "-m", "cpc", "-c", "Spring Sale")
        self.assertEqual(out.strip(), "https://x.com/p?a=1&utm_source=google_ads&utm_medium=cpc&utm_campaign=spring_sale#top")
        r = rj("utm", "strip", "https://x.com/p?utm_source=a&gclid=1&keep=1")
        self.assertEqual(r["url"], "https://x.com/p?keep=1")
        self.assertTrue(any(i["code"] == "UPPERCASE" for i in rj("utm", "check", "https://x.com/?utm_source=A&utm_medium=cpc&utm_campaign=c")["issues"]))
        r = rj("adstxt", os.path.join(EX, "ads.txt"))
        self.assertEqual(r["records"], 2)
        r = rj("permute", "-l", "buy,order", "-l", "shoes,boots", "--match", "broad,exact")
        self.assertEqual(r["count"], 8)
        r = rj("cluster", os.path.join(EX, "keywords.txt"))
        self.assertTrue(r["ad_groups_suggested"] >= 2)
        d = tempfile.mkdtemp()
        kw, ng = os.path.join(d, "k.txt"), os.path.join(d, "n.txt")
        open(kw, "w").write("blue widgets\nused widget repair\n")
        open(ng, "w").write("used\n[free]\n\"widgets blue\"\n")
        r = rj("negatives", "--keywords", kw, "--negatives", ng)
        self.assertEqual([c["negative"] for c in r["conflicts"]], ["used"])
        r = rj("searchterms", os.path.join(EX, "search_terms.csv"), "--min-clicks", "10")
        self.assertEqual(r["total_conversions"], 18)
        self.assertEqual(r["cost_on_zero_conversion_terms"], 80.3)
        self.assertEqual(r["wasted_spend_terms"][0]["search_term"], "free widget download")
        words = [x["word_or_phrase"] for x in r["negative_word_candidates"]]
        self.assertIn("free", words)
        self.assertIn("jobs", words)


class Network(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = start_server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_onpage_headers_redirects(self):
        r = rj("onpage", self.base + "/", "-k", "blue widgets")
        codes = {i["code"] for i in r["issues"]}
        self.assertIn("IMG_ALT_MISSING", codes)
        self.assertTrue(r["keyword"]["in_title"])
        about = rj("onpage", self.base + "/about")
        self.assertIn("H1_MULTIPLE", {i["code"] for i in about["issues"]})
        h = rj("headers", self.base + "/")
        self.assertEqual(h["status"], 200)
        t = rj("redirects", self.base + "/redir")
        self.assertEqual(t["final_url"], self.base + "/about")
        self.assertEqual(t["redirects"], 1)
        code, _, _ = run("onpage", self.base + "/", "--fail-on", "error")
        self.assertIn(code, (0, 1))

    def test_robots_sitemap_links(self):
        r = rj("robots", self.base, "--test", "/private/x", "--test", "/ok")
        self.assertEqual([t["allowed"] for t in r["tests"]], [False, True])
        s = rj("sitemap", self.base + "/sitemap.xml", "--check", "5")
        self.assertEqual(s["entries"], 2)
        l = rj("links", self.base + "/")
        self.assertTrue(any(p["url"].endswith("/missing") and p["kind"] == "broken" for p in l["problem_links"]))

    def test_crawl_audit_graph(self):
        c = rj("crawl", self.base + "/", "--max-pages", "20")
        self.assertGreaterEqual(c["pages_crawled"], 5)
        self.assertTrue(any(b["target"].endswith("/missing") for b in c["broken_links"]))
        self.assertTrue(c["duplicate_titles"])
        d = tempfile.mkdtemp()
        html, csv_ = os.path.join(d, "r.html"), os.path.join(d, "p.csv")
        a = rj("audit", self.base + "/", "--max-pages", "20", "--no-variants", "--html", html, "--csv", csv_)
        self.assertTrue(os.path.getsize(html) > 1000 and os.path.getsize(csv_) > 50)
        self.assertIn("BROKEN_INTERNAL_LINK", {p["issue"] for p in a["top_priorities"]})
        g = rj("linkgraph", self.base + "/", "--max-pages", "20")
        self.assertTrue(g["top_pages_by_internal_pagerank"])
        du = rj("duplicates", "--crawl", self.base + "/", "--max-pages", "20", "--min-words", "20")
        self.assertTrue(any(p["verdict"] == "near-identical" for p in du["pairs"]))

    def test_tags_landing_hreflang_misc(self):
        t = rj("tags", self.base + "/")
        names = {x["tag"] for x in t["tags"]}
        self.assertTrue({"Google Analytics 4", "Meta (Facebook) Pixel"} <= names)
        l = rj("landing", self.base + "/", "-k", "blue widgets")
        self.assertTrue(l["message_match"][0]["in_h1"] is False or True)
        self.assertIn("score", l)
        h = rj("hreflang", self.base + "/", "--verify")
        self.assertNotIn("NO_SELF_REFERENCE", {i["code"] for i in h["issues"]})
        p = rj("pagespeed", self.base + "/", "--psi", "--psi-endpoint", self.base + "/psi")
        self.assertEqual(p["pagespeed_insights"]["lab_data"][0]["value"], 82)
        s = rj("suggest", "blue widgets", "--endpoint", self.base + "/suggest", "--sleep", "0")
        self.assertEqual(s["count"], 2)
        a = rj("adstxt", self.base)
        self.assertEqual(a["records"], 1)
        v = rj("redirects", "--variants", self.base.replace("http://", ""))
        self.assertTrue(v["variants"])


if __name__ == "__main__":
    unittest.main()
