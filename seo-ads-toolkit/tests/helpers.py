"""Local HTTP test site used by the network-tool tests (no internet needed)."""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

HOME = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Acme Widgets - Buy Blue Widgets Online Today</title>
<meta name="description" content="Acme sells durable blue widgets with fast delivery, easy returns and friendly support for homes and businesses everywhere.">
<meta name="viewport" content="width=device-width"><link rel="canonical" href="http://127.0.0.1:{port}/">
<link rel="alternate" hreflang="en" href="http://127.0.0.1:{port}/"><link rel="alternate" hreflang="x-default" href="http://127.0.0.1:{port}/">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Organization","name":"Acme","url":"http://x.test"}}</script>
<script src="/a.js"></script></head><body><nav><a href="/about">About</a></nav><h1>Blue Widgets</h1>
<p>{words}</p><a href="/about">About us</a> <a href="/missing">Gone</a> <a href="/redir">Moved</a> <a href="/dup1">d1</a> <a href="/dup2">d2</a>
<img src="/i.png"><img src="/j.png" alt="x"><a href="/privacy">Privacy</a><form><input name="e"><button>Get a quote</button></form>
<script>gtag('config','G-ABC1234567');fbq('init','1234567890123');</script></body></html>"""
ABOUT = """<html lang="en"><head><title>About</title></head><body><h1>About</h1><h1>Again</h1><p>Short.</p></body></html>"""
DUP = """<html lang="en"><head><title>Same Title Here For Both Pages OK</title></head><body><h1>Dup</h1><p>{words}</p></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve()

    def _serve(self, head=False):
        port = self.server.server_address[1]
        words = " ".join(["widget quality durable blue delivery"] * 80)
        routes = {
            "/": (200, "text/html", HOME.format(port=port, words=words), {}),
            "/about": (200, "text/html", ABOUT, {}),
            "/dup1": (200, "text/html", DUP.format(words=words), {}),
            "/dup2": (200, "text/html", DUP.format(words=words), {}),
            "/privacy": (200, "text/html", "<html><head><title>Privacy policy page title</title></head><body><h1>Privacy</h1></body></html>", {}),
            "/redir": (301, "text/html", "", {"Location": "/about"}),
            "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private/\nSitemap: http://127.0.0.1:%d/sitemap.xml\n" % port, {}),
            "/sitemap.xml": (200, "application/xml", '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>http://127.0.0.1:%d/</loc><lastmod>2026-01-01</lastmod></url><url><loc>http://127.0.0.1:%d/about</loc></url></urlset>' % (port, port), {}),
            "/ads.txt": (200, "text/plain", "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n", {}),
            "/psi": (200, "application/json", '{"lighthouseResult":{"categories":{"performance":{"score":0.82}},"audits":{"largest-contentful-paint":{"numericValue":4500},"cumulative-layout-shift":{"numericValue":0.05},"total-blocking-time":{"numericValue":150},"unused-css":{"title":"Reduce unused CSS","details":{"type":"opportunity","overallSavingsMs":300,"overallSavingsBytes":20480}}}},"loadingExperience":{"metrics":{"LARGEST_CONTENTFUL_PAINT_MS":{"percentile":3000,"category":"AVERAGE"}}}}', {}),
            "/suggest": (200, "application/json", '["blue widgets",["blue widgets for sale","blue widgets near me"]]', {}),
        }
        code, ctype, body, hdr = routes.get(self.path.split("?")[0], (404, "text/html", "<h1>nf</h1>", {}))
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in hdr.items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(data)


def start_server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]
