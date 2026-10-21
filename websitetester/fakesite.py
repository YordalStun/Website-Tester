"""A small, deliberately slow and badly set up fake WordPress site on this PC, used by the self-test and tests."""
import json
import os
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def png(width, height, noisy=True):
    """A valid PNG. Noisy pixels don't compress, so the file is realistically big."""
    row = width * 3
    if noisy:
        raw = b"".join(b"\x00" + os.urandom(row) for _ in range(height))
    else:
        raw = (b"\x00" + b"\x80" * row) * height

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


HERO = None
SMALL = png(40, 40, noisy=False)

PAGE = """<!DOCTYPE html>
<html lang="en-GB"><head>
<meta charset="utf-8">
<title>Fake Plumbing Ltd - Plumbers in Testville</title>
<meta name="generator" content="WordPress 6.2.2">
<link rel="https://api.w.org/" href="/wp-json/">
<link rel="stylesheet" href="/wp-includes/css/dist/block-library/style.min.css?ver=6.2.2">
<link rel="stylesheet" href="/wp-includes/css/dashicons.min.css?ver=6.2.2">
<link rel="stylesheet" href="/wp-content/plugins/elementor/assets/css/frontend.min.css?ver=3.10.0">
<link rel="stylesheet" href="/wp-content/plugins/contact-form-7/includes/css/styles.css?ver=5.7">
<link rel="stylesheet" href="/wp-content/themes/astra/style.css?ver=4.0.0">
<link rel="stylesheet" href="/wp-content/themes/astra-child/style.css?ver=1.0">
<link rel="stylesheet" href="/wp-content/plugins/old-thing/missing.css?ver=1.0">
<link rel="stylesheet" href="/wp-content/plugins/old-thing/limited.css?ver=1.0">
<script src="/wp-includes/js/jquery/jquery.min.js?ver=3.6.4"></script>
<script src="/wp-includes/js/jquery/jquery-migrate.min.js?ver=3.4.0"></script>
<script src="/wp-content/plugins/revslider/public/assets/js/rbtools.min.js?ver=6.6.0"></script>
<script src="/wp-content/plugins/elementor/assets/js/frontend.js?ver=3.10.0"></script>
<script src="/wp-includes/js/wp-emoji-release.min.js?ver=6.2.2"></script>
</head>
<body class="home">
<br />
<b>Warning</b>:  Undefined array key "size" in <b>/home/fake/public_html/wp-content/plugins/old-thing/old-thing.php</b> on line <b>42</b><br />
<h1>Fake Plumbing</h1>
<img src="/wp-content/uploads/2023/01/hero.png" alt="hero">
<p>We fix leaks.</p>
<img src="/wp-content/uploads/2023/01/a.png"><img src="/wp-content/uploads/2023/01/b.png">
<img src="/wp-content/uploads/2023/01/c.png"><img src="/wp-content/uploads/2023/01/d.png">
<img src="/wp-content/uploads/2023/01/e.png"><img src="/wp-content/uploads/2023/01/f.png">
<img src="/wp-content/uploads/2023/01/g.png">
<script src="/wp-content/plugins/contact-form-7/includes/js/index.js?ver=5.7"></script>
<script src="/wp-content/plugins/woocommerce/assets/js/frontend/cart-fragments.min.js?ver=7.0.0" id="wc-cart-fragments-js"></script>
<script>
fetch('/wp-admin/admin-ajax.php?action=get_refreshed_fragments', {method: 'POST'});
var t = Date.now(); while (Date.now() - t < 400) {}
setTimeout(function () { var x = Date.now(); while (Date.now() - x < 300) {} }, 50);
</script>
</body></html>
<!-- page generated in 0.4 seconds -->
"""


def _js(size_kb, name):
    body = f"function {name}Used(){{return 1}}\n{name}Used();\n"
    body += "".join(f"function {name}Unused{i}(a, b) {{\n  var total = a + b;\n  return total * {i};\n}}\n"
                    for i in range(size_kb * 12))
    return body.encode()


FILES = {
    "/wp-includes/css/dist/block-library/style.min.css": (b".wp-block{margin:0}" * 300, "text/css"),
    "/wp-includes/css/dashicons.min.css": (b".dashicons{font-family:dashicons}" * 400, "text/css"),
    "/wp-content/plugins/elementor/assets/css/frontend.min.css": (b".elementor{display:block}\n" * 2000, "text/css"),
    "/wp-content/plugins/contact-form-7/includes/css/styles.css": (b".wpcf7 form { margin: 0; }\n" * 100, "text/css"),
    "/wp-content/themes/astra/style.css": (b"/*\nTheme Name: Astra\nVersion: 4.0.0\n*/\nbody{margin:0}\n", "text/css"),
    "/wp-content/themes/astra-child/style.css": (b"/*\nTheme Name: Astra Child\nTemplate: astra\nVersion: 1.0\n*/\n"
                                                 b"@import url('../astra/style.css');\n", "text/css"),
    "/wp-includes/js/jquery/jquery.min.js": (b"window.jQuery=function(){};" + b"/*pad*/" * 4000, "application/javascript"),
    "/wp-includes/js/jquery/jquery-migrate.min.js": (b"window.jqm=1;", "application/javascript"),
    "/wp-content/plugins/revslider/public/assets/js/rbtools.min.js": (_js(300, "rev"), "application/javascript"),
    "/wp-content/plugins/elementor/assets/js/frontend.js": (_js(60, "el"), "application/javascript"),
    "/wp-includes/js/wp-emoji-release.min.js": (b"window.emoji=1;", "application/javascript"),
    "/wp-content/plugins/contact-form-7/includes/js/index.js": (_js(20, "cf"), "application/javascript"),
    "/wp-content/plugins/woocommerce/assets/js/frontend/cart-fragments.min.js": (b"window.wcf=1;", "application/javascript"),
    "/wp-content/plugins/elementor/readme.txt": (b"=== Elementor ===\nStable tag: 3.10.0\n", "text/plain"),
    "/wp-content/plugins/contact-form-7/readme.txt": (b"=== Contact Form 7 ===\nStable tag: 5.7\n", "text/plain"),
    "/wp-content/plugins/revslider/readme.txt": (b"=== Slider Revolution ===\nStable tag: 6.6.0\n", "text/plain"),
    "/wp-content/debug.log": (b"[01-Jan-2026 10:00:00 UTC] PHP Warning:  Undefined array key \"size\" in "
                              b"/home/fake/public_html/wp-content/plugins/old-thing/old-thing.php on line 42\n",
                              "text/plain"),
    "/robots.txt": (b"User-agent: *\nDisallow: /wp-admin/\n", "text/plain"),
    "/favicon.ico": (SMALL, "image/png"),
}
REST_INDEX = {"namespaces": ["oembed/1.0", "wp/v2", "yoast/v1", "wc/v3", "contact-form-7/v1", "mystery-plugin/v1"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "Apache/2.4.41"
    sys_version = ""
    slow = 0.35

    def log_message(self, *args):
        pass

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Powered-By", "PHP/7.4.33")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            time.sleep(self.slow)
            return self._send(200, PAGE.encode(), "text/html; charset=UTF-8",
                              {"Link": '</wp-json/>; rel="https://api.w.org/"'})
        if path == "/wp-admin/admin-ajax.php":
            time.sleep(self.slow)
            return self._send(400, b"0", "text/html; charset=UTF-8")
        if path in ("/wp-json/", "/wp-json"):
            time.sleep(self.slow)
            return self._send(200, json.dumps(REST_INDEX).encode(), "application/json; charset=UTF-8")
        if path == "/wp-json/wp/v2/users":
            return self._send(200, b'[{"id":1,"name":"admin","slug":"admin"}]', "application/json; charset=UTF-8")
        if path == "/xmlrpc.php":
            return self._send(405, b"XML-RPC server accepts POST requests only.", "text/plain")
        if path == "/feed/":
            return self._send(200, b"<rss><channel><generator>https://wordpress.org/?v=6.2.2</generator></channel></rss>",
                              "application/rss+xml")
        if path == "/wp-content/uploads/2023/01/hero.png":
            return self._send(200, HERO, "image/png")
        if path.startswith("/wp-content/uploads/2023/01/"):
            return self._send(200, SMALL, "image/png")
        if path.endswith("/limited.css"):
            return self._send(429, b"Too Many Requests", "text/plain", {"Retry-After": "0"})
        if path in FILES:
            body, ctype = FILES[path]
            return self._send(200, body, ctype)
        time.sleep(self.slow)  # WordPress 404 pages are slow
        return self._send(404, b"<html><body>Not found</body></html>", "text/html; charset=UTF-8")


class FakeSite:
    """with FakeSite() as url: ...  - serves the fake site on 127.0.0.1."""

    def __init__(self, slow=0.35):
        global HERO
        if HERO is None:
            HERO = png(900, 600)
        Handler.slow = slow
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/"

    def __enter__(self):
        self.thread.start()
        return self.url

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
