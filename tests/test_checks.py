import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from websitetester import browser, dnscheck, findings, net, pagecheck, runner, server, wordpress
from websitetester.fakesite import FakeSite


def test_normalise():
    assert runner.normalise("example.com") == "https://example.com/"
    assert runner.normalise(" WWW.Example.co.uk/shop?x=1 ") == "https://www.example.co.uk/shop?x=1"
    assert runner.normalise("http://127.0.0.1:8080/") == "http://127.0.0.1:8080/"
    for bad in ("", "not a site", "ftp://example.com", "example", "exa mple.com"):
        with pytest.raises(ValueError):
            runner.normalise(bad)


def test_scores_and_top_fixes():
    fs = [findings.Finding("server", findings.CRITICAL, "slow", impact=500, symptom=True),
          findings.Finding("server", findings.WARNING, "php", impact=100),
          findings.Finding("dns", findings.GOOD, "ok"),
          findings.Finding("caching", findings.CRITICAL, "no cache", impact=300)]
    assert findings.category_score(fs, "server") == 60
    assert findings.category_score(fs, "dns") == 100
    assert findings.category_score(fs, "seo") is None
    assert [f.title for f in findings.top_fixes(fs)] == ["no cache", "php"]
    assert findings.grade(95) == "A" and findings.grade(10) == "F"


class _Gzip(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = gzip.compress(b"<html>" + b"hello " * 1000 + b"</html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_fetch_times_and_decompresses():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Gzip)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        r = net.fetch(f"http://127.0.0.1:{httpd.server_address[1]}/")
    finally:
        httpd.shutdown()
    assert r.ok and r.status == 200
    assert r.text.startswith("<html>hello") and r.transfer_size < len(r.body)
    assert r.timing.wait >= 0 and r.timing.total > 0
    assert net.fetch("http://127.0.0.1:1/").error


def test_parser_and_assets():
    html = """<html lang=en><head><title> Hi </title><meta name=viewport content=x>
    <link rel=stylesheet href=/a.css><script src=/b.js></script><script src=/c.js defer></script></head>
    <body><img src=/x.jpg loading=lazy width=1 height=1><img data-src=/y.jpg src="data:image/gif;base64,R0">
    <iframe src="https://www.youtube.com/embed/abc"></iframe><script>var a=1;</script></body></html>"""
    p = pagecheck.PageParser("https://example.com/page")
    p.feed(html)
    assert p.title.strip() == "Hi" and p.lang == "en"
    assert [i["url"] for i in p.images] == ["https://example.com/x.jpg", "https://example.com/y.jpg"]
    assert all(i["lazy"] for i in p.images)
    assets = pagecheck.collect_assets(p)
    blocking = {a.url.rsplit("/", 1)[1]: a.blocking for a in assets}
    assert blocking == {"a.css": True, "b.js": True, "c.js": False, "x.jpg": False, "y.jpg": False}
    assert p.iframes[0]["url"].startswith("https://www.youtube.com")


def test_robots_and_php_errors():
    assert pagecheck.blocks_everything("User-agent: *\nDisallow: /\n")
    assert not pagecheck.blocks_everything("User-agent: *\nDisallow: /wp-admin/\n")
    assert not pagecheck.blocks_everything("User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nDisallow:\n")
    errs = pagecheck.php_errors("<b>Warning</b>:  Undefined variable $x in <b>/var/www/wp-content/plugins/foo/foo.php</b>")
    assert errs and "plugins/foo/foo.php" in errs[0]


def test_wordpress_helpers():
    assert wordpress.is_older("6.2.2", "6.6") and not wordpress.is_older("6.6", "6.6.0")
    assert wordpress.owner_of("https://a.com/wp-content/plugins/elementor/x.js") == ("plugin", "elementor")
    assert wordpress.owner_of("https://a.com/wp-content/themes/astra/s.css") == ("theme", "astra")
    assert wordpress.owner_of("https://fonts.gstatic.com/x.woff2", "a.com") == ("other site", "fonts.gstatic.com")
    head = wordpress.parse_style_header("/*\nTheme Name: Astra Child\nTemplate: astra\nVersion: 1.2\n*/")
    assert head == {"theme_name": "Astra Child", "template": "astra", "version": "1.2"}
    ok, evidence = wordpress.detect('<link href="/wp-content/themes/x/style.css">', {})
    assert ok and evidence


def test_cache_status_and_location():
    r = net.Response("https://x/", headers={"cf-cache-status": "HIT"})
    assert server.cache_status(r)[0] == "HIT"
    assert server.cache_status(net.Response("https://x/", headers={"x-litespeed-cache": "miss"}))[0] == "MISS"
    assert server.cache_status(net.Response("https://x/", headers={"age": "120"}))[0] == "HIT"
    assert 340 < server.distance_km("51.5074,-0.1278", "48.8566,2.3522") < 350  # London - Paris


def test_dns_helpers():
    assert dnscheck.provider_of(["kim.ns.cloudflare.com"]) == "Cloudflare"
    assert dnscheck.provider_of(["ns1.example.net"]) == ""
    data, fs = dnscheck.check("127.0.0.1")
    assert data.get("skipped") and not fs


def test_unused_js_coverage():
    coverage = [{"url": "https://a.com/x.js", "functions": [
        {"ranges": [{"startOffset": 0, "endOffset": 2000, "count": 1}]},
        {"ranges": [{"startOffset": 100, "endOffset": 1100, "count": 0},
                    {"startOffset": 500, "endOffset": 600, "count": 1}]}]}]
    out = browser._unused_js(coverage)
    assert out == [{"url": "https://a.com/x.js", "type": "js", "total": 2000, "unused": 900}]


def _fake_wporg(url, timeout=15, **kw):
    if "version-check" in url:
        return {"offers": [{"current": "6.8.3"}]}
    if "themes/info" in url:
        return {"name": "Astra", "version": "4.8.0"}
    if "slug]=revslider" in url:
        raise OSError("HTTP Error 404: Not Found")
    if "slug]=elementor" in url:
        return {"name": "Elementor Website Builder", "version": "3.30.0", "last_updated": "2025-09-01 10:00am GMT"}
    if "slug]=contact-form-7" in url:
        return {"name": "Contact Form 7", "version": "5.7", "last_updated": "2019-01-01 10:00am GMT"}
    if "slug]=" in url:
        return {"name": "Something", "version": "1.0", "last_updated": "2026-01-01"}
    raise OSError("offline")


def test_full_run_against_fake_site(monkeypatch):
    monkeypatch.setattr(net, "get_json", _fake_wporg)
    with FakeSite(slow=0.4) as url:
        r = runner.run(url, runner.Options(browser=False, pagespeed=False, location=False, samples=2))
    titles = [f.title for f in r.findings]
    joined = " | ".join(titles)
    assert r.is_wp and r.wp["version"] == "6.2.2" and r.wp["latest_version"] == "6.8.3"
    assert "WordPress is out of date (6.2.2, latest is 6.8.3)" in titles
    assert r.wp["theme"]["name"] == "Astra Child" and r.wp["theme"]["child_of"] == "astra"
    plugins = r.wp["plugins"]
    assert {"elementor", "contact-form-7", "revslider", "woocommerce", "wordpress-seo"} <= set(plugins)
    assert plugins["revslider"]["name"] == "Slider Revolution" and not plugins["revslider"]["on_wporg"]
    assert "1 plugin is out of date" in joined and "haven't been updated" not in joined
    assert "1 plugin hasn't been updated by the author in over 2 years" in titles
    assert "mystery-plugin/v1" in r.wp["unknown_namespaces"]
    for expected in ("No page caching found", "Error messages are shown on the page", "Old, unsupported PHP version",
                     "The WordPress error log is public", "XML-RPC is switched on", "Usernames are publicly listed",
                     "cart fragments", "CSS uses @import", "The site doesn't use HTTPS", "No mobile viewport tag",
                     "missing or broken"):
        assert expected in joined, expected
    broken = next(f for f in r.findings if "missing or broken" in f.title)
    assert not any("limited.css" in item for item in broken.items)
    throttled = next(f for f in r.findings if f.title == "The site's firewall slowed this test down")
    assert any("limited.css" in item for item in throttled.items)
    s = r.server
    assert s["home"]["think"] >= 150 and s["static"]["think"] < 100 and s["wp_backend"]["think"] >= 150
    assert r.diagnosis["headline"] == "Main problem: WordPress (no page cache)"
    assert "it's the WordPress side" in r.diagnosis["paragraphs"][0]
    from websitetester import export, report
    text = export.ai_export(r)
    for section in ("## About this file", "## Findings", "## Every file the page uses", "## WordPress, theme and plugins"):
        assert section in text
    assert "Slider Revolution" in text and "/wp-content/plugins/revslider/" in text
    html = report.html_report(r)
    assert "Fix these first" in html and "Slider Revolution" in html
    assert "<script" not in report.summary_html(r)


def test_unreachable_site_scores_f():
    r = runner.run("http://127.0.0.1:1/", runner.Options(browser=False, pagespeed=False, location=False))
    assert r.grade == "F" and r.diagnosis["headline"] == "The website couldn't be reached"
    assert any(f.title == "The website couldn't be reached" for f in r.findings)


@pytest.mark.skipif(not browser.find_browser()[0], reason="no Edge/Chrome/Chromium installed")
def test_real_browser_on_fake_site():
    with FakeSite(slow=0.1) as url:
        results = browser.run(url, profiles=(browser.DESKTOP,))
    d = results["desktop"]
    assert not d.get("error"), d.get("error")
    assert d["lcp"] and d["fcp"] and d["dom"] > 10
    assert any("admin-ajax.php" in c["url"] for c in d["backend_calls"])
    assert any("revslider" in c["url"] and c["unused"] > c["total"] * 0.8 for c in d["coverage"])
    assert d["screenshot"]
