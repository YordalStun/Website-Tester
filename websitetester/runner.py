"""Runs the whole test, step by step, and collects everything into one Result."""
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from . import browser, diagnosis, dnscheck, net, pagecheck, pagespeed, server, wordpress
from .findings import CRITICAL, INFO, Finding, grade, overall_score


@dataclass
class Options:
    dns: bool = True
    browser: bool = True
    mobile: bool = True
    pagespeed: bool = True
    pagespeed_key: str = ""
    check_updates: bool = True  # ask WordPress.org about plugin/theme versions
    location: bool = True  # look up where the server is (ipinfo.io)
    samples: int = 5


@dataclass
class Result:
    target: str
    start_url: str
    started: datetime = field(default_factory=datetime.now)
    duration: float = 0.0
    chain: list = field(default_factory=list)
    page: net.Response = None
    final_url: str = ""
    site_host: str = ""
    is_wp: bool = False
    wp_evidence: list = field(default_factory=list)
    dns: dict = field(default_factory=dict)
    server: dict = field(default_factory=dict)
    ssl: dict = field(default_factory=dict)
    redirects: dict = field(default_factory=dict)
    wp: dict = field(default_factory=dict)
    content: dict = field(default_factory=dict)
    seo: dict = field(default_factory=dict)
    assets: list = field(default_factory=list)
    parser: pagecheck.PageParser = None
    browser: dict = field(default_factory=dict)
    psi: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    diagnosis: dict = field(default_factory=dict)
    breakdown: list = field(default_factory=list)
    score: int = None
    grade: str = "?"
    cancelled: bool = False

    @property
    def domain(self):
        return urlsplit(self.final_url or self.start_url).hostname or self.target


def normalise(target):
    """'example.com' / 'www.example.com/page' / 'https://...' -> a full address. Raises ValueError."""
    target = (target or "").strip().strip("'\"")
    if not target:
        raise ValueError("Please type a website address, for example example.com")
    if "://" not in target:
        target = "https://" + target
    parts = urlsplit(target)
    if parts.scheme not in ("http", "https"):
        raise ValueError("The address must start with http:// or https:// (or just type the domain)")
    host = parts.hostname or ""
    if not (dnscheck.is_ip(host) or host == "localhost"):
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_host = ""
        if "." not in ascii_host or not re.fullmatch(r"[a-z0-9.\-]+", ascii_host):
            raise ValueError(f"\"{host or target}\" doesn't look like a website address")
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc.lower()}{path}" + (f"?{parts.query}" if parts.query else "")


STEPS = [  # (key, share of the progress bar)
    ("dns", 6), ("page", 6), ("ssl", 3), ("assets", 12), ("wordpress", 14), ("server", 22), ("seo", 3),
    ("browser", 30), ("pagespeed", 4),
]


class Runner:
    def __init__(self, target, options=None, progress=None, cancel_event=None):
        self.options = options or Options()
        self.cancel_event = cancel_event or threading.Event()
        self._progress = progress
        self.r = Result(target=target, start_url=normalise(target))
        self._done = 0
        self._total = sum(w for _, w in STEPS)

    def cancelled(self):
        return self.cancel_event.is_set()

    def progress(self, text, step_fraction=0.0, weight=0):
        if self._progress:
            pct = min(99, int((self._done + weight * step_fraction) * 100 / self._total))
            self._progress(text, pct)

    def _step(self, key, text, fn):
        if self.cancelled():
            return None
        weight = dict(STEPS)[key]
        self.progress(text, 0, weight)
        try:
            return fn(weight)
        except Exception as e:  # one broken step must never stop the rest of the test
            self.r.errors.append(f"{text}: {e}")
            self.r.errors.append(traceback.format_exc(limit=4))
            return None
        finally:
            self._done += weight

    def add(self, findings):
        self.r.findings.extend(findings or [])

    def run(self):
        r = self.r
        started = time.time()
        host = urlsplit(r.start_url).hostname

        if self.options.dns:
            self._step("dns", f"Checking DNS for {host}", lambda w: self._dns(host))
        page_ok = self._step("page", f"Visiting {r.start_url}", lambda w: self._page())
        if page_ok:
            psi_thread = None
            if self.options.pagespeed:
                psi_thread = threading.Thread(target=self._pagespeed, daemon=True)
                psi_thread.start()
            self._step("ssl", "Checking the SSL certificate and redirects", lambda w: self._ssl())
            self._step("assets", "Downloading every file the page uses", self._assets)
            if r.is_wp:
                self._step("wordpress", "Checking WordPress, the theme and plugins", self._wordpress)
            else:
                self._done += dict(STEPS)["wordpress"]
            self._step("server", "Timing the server", self._server)
            self._step("seo", "Checking SEO basics and security headers", lambda w: self._seo())
            if self.options.browser:
                self._step("browser", "Loading the page in a real browser", self._browser)
            else:
                self._done += dict(STEPS)["browser"]
            if psi_thread:
                self.progress("Waiting for Google PageSpeed", 0, 0)
                while psi_thread.is_alive() and not self.cancelled():
                    psi_thread.join(0.5)
            self._done += dict(STEPS)["pagespeed"]
            self.add(pagespeed.findings_for(r.psi) if self.options.pagespeed and r.psi else [])
        r.cancelled = self.cancelled()
        if self._progress:
            self._progress("Working out the diagnosis", 99)
        try:
            r.diagnosis = diagnosis.diagnose(r)
            r.breakdown = diagnosis.breakdown(r)
        except Exception as e:
            r.errors.append(f"diagnosis: {e}\n{traceback.format_exc(limit=4)}")
            r.diagnosis = {"headline": "Test finished", "paragraphs": [], "causes": []}
        r.score = overall_score(r.findings)
        r.grade = grade(r.score)
        r.duration = time.time() - started
        if self._progress:
            self._progress("Done", 100)
        return r

    # --- the steps
    def _dns(self, host):
        data, findings = dnscheck.check(host)
        self.r.dns = data
        self.add(findings)

    def _page(self):
        r = self.r
        r.chain = net.follow(r.start_url)
        page = r.chain[-1]
        if page.error and r.start_url.startswith("https://") and len(r.chain) == 1:
            # Maybe the site has no HTTPS at all - try plain http so we can still test it
            alt = net.follow("http://" + r.start_url.split("://", 1)[1])
            if not alt[-1].error:
                self.add([Finding("ssl", CRITICAL, "HTTPS doesn't work on this site",
                                  f"Connecting with https:// failed ({page.error}), but http:// works.",
                                  "Install an SSL certificate (free from Let's Encrypt in most hosting control "
                                  "panels) and redirect http to https.")])
                r.chain, page = alt, alt[-1]
        r.page = page
        r.final_url = page.url
        r.site_host = net.host_of(page.url)
        if page.error:
            self.add([Finding("server", CRITICAL, "The website couldn't be reached",
                              f"{page.url}: {page.error}.",
                              "Check the site is up, the domain hasn't expired and the DNS points at the right "
                              "server.", impact=100000)])
            return False
        if page.status >= 400:
            blocked = page.status in (403, 429, 503) and ("cf-mitigated" in page.headers or "captcha" in page.text.lower()
                                                          or "challenge" in page.text.lower()[:20000])
            self.add([Finding(
                "server", CRITICAL, f"The page returned an error ({page.status} {page.reason})",
                "A firewall/bot protection blocked this test - results below may only describe the block page."
                if blocked else "Visitors get an error instead of the page.",
                "If a security service (Cloudflare, Wordfence, Sucuri) blocked the test, allow this PC's IP address "
                "while testing." if blocked else "Check the server's error log in your hosting control panel.",
                impact=100000)])
        r.parser = pagecheck.PageParser(page.url)
        try:
            r.parser.feed(page.text)
        except Exception as e:  # badly broken HTML - carry on with what was parsed
            r.errors.append(f"HTML parsing: {e}")
        r.is_wp, r.wp_evidence = wordpress.detect(page.text, page.headers)
        errors = pagecheck.php_errors(page.text)
        if errors:
            self.add([Finding(
                "wordpress" if r.is_wp else "server", CRITICAL, "Error messages are shown on the page",
                "PHP errors/warnings are printed into the page for visitors to see. Something (usually a plugin or "
                "the theme) is broken or not compatible with the server's PHP version - and printing errors slows "
                "every page down.",
                "Update the plugin/theme named in the file path below (or replace it). Then turn off error display: "
                "set WP_DEBUG and WP_DEBUG_DISPLAY to false in wp-config.php.", impact=500, items=errors)])
        return True

    def _ssl(self):
        r = self.r
        data, findings = server.check_ssl(r.page, r.site_host)
        r.ssl = data
        self.add(findings)
        findings, r.redirects = server.check_redirects(r.start_url, r.chain)
        self.add(findings)
        if r.final_url.startswith("https://"):
            ok, findings = server.check_http_redirect(urlsplit(r.start_url).hostname)
            r.ssl["http_redirects"] = ok
            self.add(findings)

    def _assets(self, weight):
        r = self.r
        assets = pagecheck.collect_assets(r.parser)
        pagecheck.fetch_assets(
            assets, cancelled=self.cancelled,
            progress=lambda done, total: self.progress(f"Downloading files ({done} of {total})", done / max(total, 1), weight))
        r.assets = assets
        findings, r.content = pagecheck.analyze(r.page, r.parser, assets, r.site_host)
        self.add(findings)

    def _wordpress(self, weight):
        r = self.r
        r.wp, findings = wordpress.check(
            r.final_url, r.page, r.parser, r.assets, check_updates=self.options.check_updates,
            progress=lambda done, total: self.progress(f"Checking plugins ({done} of {total})", done / max(total, 1), weight))
        self.add(findings)

    def _static_url(self):
        """A plain file on the same server (no WordPress involved) for comparison."""
        best = None
        for a in self.r.assets:
            if a.failed or net.host_of(a.url) != self.r.site_host or a.resp.status != 200:
                continue
            path = urlsplit(a.url).path.lower()
            if a.kind in ("css", "js", "image") and not path.endswith(".php"):
                score = 0 if "/wp-includes/" in path else 1 if a.kind == "image" else 2
                if best is None or score < best[0]:
                    best = (score, a.url)
        if best:
            return best[1]
        fav = net.fetch(f"{urlsplit(self.r.final_url).scheme}://{self.r.site_host}/favicon.ico", read_body=False)
        if fav.status == 200 and "image" in fav.content_type:
            return fav.url
        return None

    def _server(self, weight):
        r = self.r
        count = [0]

        def step(text):
            count[0] += 1
            self.progress(text, min(count[0] / 5, 1), weight)
        data, findings = server.check_server(r.page, r.chain, r.is_wp, static_url=self._static_url(), progress=step,
                                             cancelled=self.cancelled, samples=self.options.samples)
        if not self.options.location:
            for key in ("tester_location", "server_location"):
                data.pop(key, None)
        r.server = data
        self.add(findings)

    def _seo(self):
        r = self.r
        findings, r.seo = pagecheck.check_seo_and_headers(r.page, r.parser, r.final_url)
        self.add(findings)

    def _browser(self, weight):
        r = self.r
        profiles = [browser.DESKTOP] + ([browser.MOBILE] if self.options.mobile else [])
        path, name = browser.find_browser()
        if not path:
            self.add([Finding("browser", INFO, "Real browser test skipped",
                              "Microsoft Edge or Google Chrome wasn't found on this PC.")])
            return
        count = [0]

        def step(text):
            self.progress(f"{text} with {name}", count[0] / len(profiles), weight)
            count[0] += 1
        r.browser = browser.run(r.final_url, profiles, progress=step, cancelled=self.cancelled)
        known = {a.url for a in r.assets if a.failed}
        self.add(diagnosis.browser_findings(r.browser, known_broken=known, site_host=r.site_host))
        desktop = r.browser.get("desktop") or {}
        if not desktop.get("error"):
            self.add(wordpress.plugin_findings_from_browser(desktop, r.wp, r.site_host))

    def _pagespeed(self):
        try:
            raw = pagespeed.run(self.r.final_url, "mobile", self.options.pagespeed_key)
            self.r.psi = pagespeed.summarise(raw)
        except Exception as e:
            self.r.psi = {"error": str(e)}


def run(target, options=None, progress=None, cancel_event=None):
    return Runner(target, options, progress, cancel_event).run()


def plain_text_summary(r):
    """Short text version (for the command line and CI logs)."""
    from .findings import CATEGORY_TITLES, top_fixes
    lines = [f"Website Tester report for {r.final_url or r.start_url}",
             f"Overall: {r.grade} ({r.score if r.score is not None else '?'}/100)   took {r.duration:.0f} s", "",
             r.diagnosis.get("headline", "")]
    lines += ["  " + p for p in r.diagnosis.get("paragraphs", [])]
    lines += ["", "Where the time goes (first visit, desktop):"]
    for label, ms, _ in r.breakdown:
        lines.append(f"  {label:<55} {ms:8.0f} ms")
    lines += ["", "Fix these first:"]
    for f in top_fixes(r.findings, 12):
        lines.append(f"  [{f.severity.upper()}] ({CATEGORY_TITLES.get(f.category, f.category)}) {f.title}")
    lines += ["", f"WordPress: {'yes' if r.is_wp else 'no'}"]
    if r.is_wp:
        wp = r.wp or {}
        lines.append(f"  version {wp.get('version') or '?'} (latest {wp.get('latest_version') or '?'}), "
                     f"theme {(wp.get('theme') or {}).get('name', '?')}, {len(wp.get('plugins') or {})} plugins seen")
    for name in ("desktop", "mobile"):
        d = (r.browser or {}).get(name)
        if d and not d.get("error"):
            lines.append(f"Browser {name}: LCP {d.get('lcp') or 0:.0f} ms, FCP {d.get('fcp') or 0:.0f} ms, "
                         f"CLS {d.get('cls') or 0:.3f}, TBT {d.get('tbt') or 0:.0f} ms, "
                         f"{len(d.get('requests') or [])} requests, {d.get('total_bytes', 0) // 1024} KB")
        elif d:
            lines.append(f"Browser {name}: {d.get('error')}")
    if r.psi:
        lines.append(f"Google PageSpeed: {r.psi.get('score', r.psi.get('error'))}")
    lines += ["", f"All findings ({len(r.findings)}):"]
    for f in sorted(r.findings, key=lambda f: (f.category, f.sort_key)):
        lines.append(f"  {f.category:<10} {f.severity:<8} {f.title}")
    if r.errors:
        lines += ["", "Parts of the test that failed:"] + ["  " + e.replace("\n", "\n  ") for e in r.errors]
    return "\n".join(lines)
