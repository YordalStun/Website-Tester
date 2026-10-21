"""Load the page in a real browser (Microsoft Edge or Google Chrome, run hidden) and measure what visitors see.

Talks to the browser over the Chrome DevTools Protocol, the same way Google's Lighthouse does.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

import websocket

DESKTOP = {"name": "desktop", "width": 1350, "height": 940, "scale": 1, "mobile": False}
MOBILE = {"name": "mobile", "width": 412, "height": 823, "scale": 2.625, "mobile": True,
          # Same "slow 4G + mid-range phone" Google PageSpeed uses
          "latency": 150, "down_kbps": 1638, "up_kbps": 750, "cpu": 4,
          "ua": ("Mozilla/5.0 (Linux; Android 11; moto g power (2022)) AppleWebKit/537.36 (KHTML, like Gecko) "
                 "Chrome/141.0.0.0 Mobile Safari/537.36")}

OBSERVER_JS = r"""
(() => {
  const s = window.__wt = {lcp: null, cls: 0, shifts: [], longtasks: []};
  const describe = (el) => {
    if (!el || !el.tagName) return '';
    let d = el.tagName.toLowerCase();
    if (el.id) d += '#' + el.id;
    if (typeof el.className === 'string' && el.className.trim()) d += '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.');
    return d.slice(0, 120);
  };
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        s.lcp = {t: e.startTime, size: e.size, url: e.url || '', el: describe(e.element),
                 text: (e.element && !e.url) ? (e.element.textContent || '').trim().slice(0, 100) : '',
                 lazy: !!(e.element && e.element.getAttribute && e.element.getAttribute('loading') === 'lazy')};
      }
    }).observe({type: 'largest-contentful-paint', buffered: true});
  } catch (e) {}
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (e.hadRecentInput) continue;
        s.cls += e.value;
        if (s.shifts.length < 30) s.shifts.push({v: e.value, t: e.startTime,
          els: (e.sources || []).map(x => describe(x.node)).filter(Boolean)});
      }
    }).observe({type: 'layout-shift', buffered: true});
  } catch (e) {}
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) s.longtasks.push([e.startTime, e.duration]);
    }).observe({type: 'longtask', buffered: true});
  } catch (e) {}
})();
"""

COLLECT_JS = r"""
(() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paint = {};
  performance.getEntriesByType('paint').forEach(p => paint[p.name] = p.startTime);
  const imgs = Array.from(document.images).slice(0, 400).map(i => {
    const r = i.getBoundingClientRect();
    return {src: i.currentSrc || i.src, nw: i.naturalWidth, nh: i.naturalHeight, w: Math.round(r.width),
            h: Math.round(r.height), top: Math.round(r.top + window.scrollY), loading: i.getAttribute('loading') || '',
            sized: i.hasAttribute('width') && i.hasAttribute('height'), visible: r.width > 0 && r.height > 0};
  });
  return {
    nav: {ttfb: nav.responseStart, responseEnd: nav.responseEnd, dcl: nav.domContentLoadedEventEnd,
          load: nav.loadEventEnd, dns: nav.domainLookupEnd - nav.domainLookupStart,
          connect: nav.connectEnd - nav.connectStart,
          tls: nav.secureConnectionStart > 0 ? nav.connectEnd - nav.secureConnectionStart : 0,
          redirect: nav.redirectEnd - nav.redirectStart, requestStart: nav.requestStart,
          protocol: nav.nextHopProtocol, transfer: nav.transferSize},
    fcp: paint['first-contentful-paint'] || null,
    wt: window.__wt || null,
    dom: document.getElementsByTagName('*').length,
    vw: window.innerWidth, vh: window.innerHeight, dpr: window.devicePixelRatio,
    imgs: imgs, title: document.title, url: location.href,
    pageHeight: document.documentElement.scrollHeight
  };
})()
"""


class BrowserError(Exception):
    pass


def find_browser():
    """(path, friendly name) of an installed Edge/Chrome, or (None, None)."""
    override = os.environ.get("WT_BROWSER")
    if override and os.path.exists(override):
        return override, os.path.basename(override)
    candidates = []
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append((os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"), "Microsoft Edge"))
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates.append((os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"), "Google Chrome"))
    elif sys.platform == "darwin":
        candidates += [("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "Microsoft Edge"),
                       ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "Google Chrome")]
    else:
        for name, label in (("microsoft-edge", "Microsoft Edge"), ("google-chrome", "Google Chrome"),
                            ("chromium", "Chromium"), ("chromium-browser", "Chromium")):
            path = shutil.which(name)
            if path:
                candidates.append((path, label))
        for path in sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")):
            candidates.append((path, "Chromium"))
    for path, label in candidates:
        if path and os.path.exists(path):
            return path, label
    return None, None


class CDP:
    """Minimal Chrome DevTools Protocol client over a websocket."""

    def __init__(self, ws_url, on_event):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True, enable_multithread=False)
        self.on_event = on_event
        self.next_id = 0

    def _read(self, timeout):
        self.ws.settimeout(max(0.05, timeout))
        try:
            raw = self.ws.recv()
        except websocket.WebSocketTimeoutException:
            return None
        msg = json.loads(raw)
        if "method" in msg and "id" not in msg:
            self.on_event(msg["method"], msg.get("params", {}))
        return msg

    def send(self, method, params=None, timeout=30.0):
        self.next_id += 1
        my_id = self.next_id
        self.ws.send(json.dumps({"id": my_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrowserError(f"the browser didn't answer ({method})")
            msg = self._read(min(left, 1.0))
            if msg and msg.get("id") == my_id:
                if "error" in msg:
                    raise BrowserError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def pump(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._read(min(0.25, end - time.monotonic()))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


class Recorder:
    """Collects network and console events during a page load."""

    def __init__(self):
        self.requests = {}
        self.finished = []
        self.in_flight = set()
        self.last_activity = time.monotonic()
        self.load_fired = False
        self.dcl_fired = False
        self.console = []
        self.stylesheets = {}

    def __call__(self, method, p):
        now = time.monotonic()
        if method == "Network.requestWillBeSent":
            rid = p["requestId"]
            if p.get("redirectResponse") and rid in self.requests:
                old = self.requests.pop(rid)
                old.update(status=p["redirectResponse"].get("status"), end=p["timestamp"], redirect=True,
                           size=p["redirectResponse"].get("encodedDataLength", 0))
                self.finished.append(old)
            if p.get("type") in ("WebSocket", "EventSource"):
                return
            self.requests[rid] = {"url": p["request"]["url"], "type": p.get("type", ""), "start": p["timestamp"],
                                  "method": p["request"].get("method", "GET"), "status": 0, "size": 0,
                                  "initiator": p.get("initiator", {}).get("type", "")}
            self.in_flight.add(rid)
            self.last_activity = now
        elif method == "Network.responseReceived":
            r = self.requests.get(p["requestId"])
            if r is not None:
                resp = p["response"]
                timing = resp.get("timing") or {}
                wait = None
                if timing:
                    wait = timing.get("receiveHeadersEnd", 0) - timing.get("sendEnd", 0)
                r.update(status=resp.get("status", 0), mime=resp.get("mimeType", ""),
                         protocol=resp.get("protocol", ""), ip=resp.get("remoteIPAddress", ""),
                         cached=resp.get("fromDiskCache") or resp.get("fromServiceWorker"), wait=wait,
                         encoding=(resp.get("headers") or {}).get("content-encoding", "") or
                                  (resp.get("headers") or {}).get("Content-Encoding", ""))
            self.last_activity = now
        elif method == "Network.loadingFinished":
            r = self.requests.pop(p["requestId"], None)
            if r is not None:
                r.update(end=p["timestamp"], size=p.get("encodedDataLength", 0))
                self.finished.append(r)
            self.in_flight.discard(p["requestId"])
            self.last_activity = now
        elif method == "Network.loadingFailed":
            r = self.requests.pop(p["requestId"], None)
            if r is not None:
                r.update(end=p["timestamp"], failed=p.get("errorText", "failed"), canceled=p.get("canceled", False),
                         blocked=p.get("blockedReason", ""))
                self.finished.append(r)
            self.in_flight.discard(p["requestId"])
            self.last_activity = now
        elif method == "Page.loadEventFired":
            self.load_fired = True
        elif method == "Page.domContentEventFired":
            self.dcl_fired = True
        elif method == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails", {})
            text = (d.get("exception") or {}).get("description") or d.get("text", "")
            self._console("JavaScript error", text.split("\n")[0], d.get("url", ""))
        elif method == "Runtime.consoleAPICalled" and p.get("type") == "error":
            args = p.get("args") or []
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in args[:3])
            self._console("console error", text, "")
        elif method == "Log.entryAdded":
            e = p.get("entry", {})
            if e.get("level") == "error" and e.get("source") != "network":
                self._console(e.get("source", "error"), e.get("text", ""), e.get("url", ""))
        elif method == "CSS.styleSheetAdded":
            h = p.get("header", {})
            self.stylesheets[h.get("styleSheetId")] = {"url": h.get("sourceURL", ""), "length": h.get("length", 0)}

    def _console(self, kind, text, url):
        if len(self.console) < 50:
            entry = {"kind": kind, "text": text[:300], "url": url}
            if entry not in self.console:
                self.console.append(entry)


def _unused_js(coverage):
    """Bytes of each script that never ran, from V8 block coverage."""
    out = []
    for script in coverage:
        url = script.get("url", "")
        funcs = script.get("functions") or []
        if not url.startswith("http") or not funcs or not funcs[0].get("ranges"):
            continue
        length = max(r["endOffset"] for f in funcs for r in f["ranges"])
        if length < 1024:
            continue
        used = bytearray(length)
        ranges = sorted((r for f in funcs for r in f["ranges"]), key=lambda r: (r["startOffset"], -r["endOffset"]))
        for r in ranges:
            s, e = r["startOffset"], min(r["endOffset"], length)
            used[s:e] = (b"\x01" if r["count"] > 0 else b"\x00") * (e - s)
        unused = used.count(0)
        out.append({"url": url, "type": "js", "total": length, "unused": unused})
    return out


def _unused_css(rule_usage, stylesheets):
    used = {}
    for r in rule_usage:
        if r.get("used"):
            used[r["styleSheetId"]] = used.get(r["styleSheetId"], 0) + (r["endOffset"] - r["startOffset"])
    out = []
    for sid, info in stylesheets.items():
        if not info["url"].startswith("http") or info["length"] < 1024:
            continue
        total = int(info["length"])
        out.append({"url": info["url"], "type": "css", "total": total, "unused": max(0, total - used.get(sid, 0))})
    return out


def _cpu_by_url(profile):
    nodes = profile.get("nodes") or []
    samples = profile.get("samples") or []
    if not nodes or not samples:
        return {}
    per_sample_ms = (profile["endTime"] - profile["startTime"]) / 1000.0 / len(samples)
    out = {}
    for node in nodes:
        url = node.get("callFrame", {}).get("url", "")
        hits = node.get("hitCount", 0)
        if url and hits:
            out[url] = out.get(url, 0) + hits * per_sample_ms
    return out


class Browser:
    def __init__(self, path=None):
        self.path, self.name = (path, os.path.basename(path)) if path else find_browser()
        if not self.path:
            raise BrowserError("Microsoft Edge or Google Chrome wasn't found on this PC")
        self.proc = None
        self.profile = None
        self.port = None
        self.browser_ws = None

    def start(self, timeout=30):
        self.profile = tempfile.mkdtemp(prefix="websitetester-")
        args = [self.path, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--disable-background-networking", "--disable-component-update", "--disable-sync",
                "--disable-default-apps", "--mute-audio", "--hide-scrollbars", "--disable-breakpad",
                "--disable-features=Translate,MediaRouter,OptimizationHints,msEdgeSidebarV2,EdgeCollections",
                "--window-size=1350,940", "about:blank"]
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.insert(1, "--no-sandbox")
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     stdin=subprocess.DEVNULL, creationflags=flags)
        port_file = os.path.join(self.profile, "DevToolsActivePort")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise BrowserError(f"{self.name} closed straight away (exit code {self.proc.returncode})")
            try:
                with open(port_file, encoding="utf-8") as f:
                    first, second = f.readline().strip(), f.readline().strip()
                if first.isdigit() and second:
                    self.port = int(first)
                    self.browser_ws = f"ws://127.0.0.1:{self.port}{second}"
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise BrowserError(f"{self.name} didn't start in time")

    def _page_ws(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
        try:
            with opener.open(req, timeout=10) as r:
                return json.loads(r.read())["webSocketDebuggerUrl"]
        except Exception:
            with opener.open(f"http://127.0.0.1:{self.port}/json/list", timeout=10) as r:
                pages = [t for t in json.loads(r.read()) if t.get("type") == "page"]
            if not pages:
                raise BrowserError("no browser tab to use")
            return pages[0]["webSocketDebuggerUrl"]

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.profile:
            for _ in range(10):
                shutil.rmtree(self.profile, ignore_errors=True)
                if not os.path.exists(self.profile):
                    break
                time.sleep(0.3)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def warm_up(self, url, timeout=20):
        """A brand-new browser does a lot of one-off work on its very first request (Edge checks proxy settings
        and SmartScreen, for example) - over a second that has nothing to do with the website. Get that out of the
        way with one tiny request (robots.txt) so it isn't counted."""
        parts = urlsplit(url)
        cdp = CDP(self._page_ws(), lambda method, params: None)
        try:
            loaded = []
            cdp.on_event = lambda method, params: loaded.append(method) if method == "Page.loadEventFired" else None
            cdp.send("Page.enable")
            cdp.send("Page.navigate", {"url": f"{parts.scheme}://{parts.netloc}/robots.txt"}, timeout=timeout)
            deadline = time.monotonic() + timeout
            while not loaded and time.monotonic() < deadline:
                cdp.pump(0.25)
        except BrowserError:
            pass
        finally:
            cdp.close()

    def load(self, url, profile=DESKTOP, deep=False, timeout=60, cancelled=None):
        """Load url like a first-time visitor and return the measurements. Each load gets its own fresh browser
        context (like a private window): empty cache, no cookies and no open connections."""
        rec = Recorder()
        control = CDP(self.browser_ws, lambda method, params: None)
        context = None
        try:
            context = control.send("Target.createBrowserContext", {"disposeOnDetach": False})["browserContextId"]
            target = control.send("Target.createTarget", {"url": "about:blank", "browserContextId": context})
            cdp = CDP(f"ws://127.0.0.1:{self.port}/devtools/page/{target['targetId']}", rec)
            try:
                return self._load(cdp, rec, url, profile, deep, timeout, cancelled)
            finally:
                cdp.close()
        finally:
            if context:
                try:
                    control.send("Target.disposeBrowserContext", {"browserContextId": context})
                except BrowserError:
                    pass
            control.close()

    def _load(self, cdp, rec, url, profile, deep, timeout, cancelled):
        for domain in ("Page", "Network", "Runtime", "Log"):
            cdp.send(f"{domain}.enable")
        cdp.send("Performance.enable", {"timeDomain": "timeTicks"})
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": OBSERVER_JS})
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        cdp.send("Emulation.setDeviceMetricsOverride", {
            "width": profile["width"], "height": profile["height"], "deviceScaleFactor": profile["scale"],
            "mobile": profile["mobile"]})
        if profile["mobile"]:
            cdp.send("Emulation.setUserAgentOverride", {"userAgent": profile["ua"]})
            cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True})
            cdp.send("Network.emulateNetworkConditions", {
                "offline": False, "latency": profile["latency"],
                "downloadThroughput": profile["down_kbps"] * 1024 / 8, "uploadThroughput": profile["up_kbps"] * 1024 / 8})
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": profile["cpu"]})
        if deep:
            cdp.send("DOM.enable")
            cdp.send("CSS.enable")
            cdp.send("CSS.startRuleUsageTracking")
            cdp.send("Profiler.enable")
            cdp.send("Profiler.startPreciseCoverage", {"callCount": False, "detailed": True})
            cdp.send("Profiler.setSamplingInterval", {"interval": 500})
            cdp.send("Profiler.start")

        started = time.monotonic()
        result = cdp.send("Page.navigate", {"url": url}, timeout=timeout)
        if result.get("errorText"):
            raise BrowserError(f"the page couldn't be opened ({result['errorText']})")
        load_deadline = started + timeout
        while not rec.load_fired and time.monotonic() < load_deadline:
            if cancelled and cancelled():
                raise BrowserError("cancelled")
            cdp.pump(0.25)
        load_timed_out = not rec.load_fired
        # Let late requests (lazy images, delayed scripts) finish: wait for 2 s of quiet, max 12 s
        quiet_deadline = time.monotonic() + 12
        while time.monotonic() < quiet_deadline:
            cdp.pump(0.25)
            if not rec.in_flight and time.monotonic() - rec.last_activity > 2.0:
                break
        wall = (rec.last_activity - started) * 1000.0

        data = cdp.send("Runtime.evaluate", {"expression": COLLECT_JS, "returnByValue": True})
        data = (data.get("result") or {}).get("value") or {}
        metrics = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics").get("metrics", [])}
        out = {"profile": profile["name"], "url": url, "load_timed_out": load_timed_out, "wall_ms": wall,
               "metrics": metrics, "console": rec.console}
        out.update(data)

        if deep:
            try:
                prof = cdp.send("Profiler.stop", timeout=60).get("profile", {})
                out["js_cpu_by_url"] = _cpu_by_url(prof)
            except BrowserError:
                out["js_cpu_by_url"] = {}
            try:
                cov = cdp.send("Profiler.takePreciseCoverage", timeout=60).get("result", [])
                cdp.send("Profiler.stopPreciseCoverage")
                usage = cdp.send("CSS.stopRuleUsageTracking", timeout=60).get("ruleUsage", [])
                out["coverage"] = _unused_js(cov) + _unused_css(usage, rec.stylesheets)
            except BrowserError:
                out["coverage"] = []
        try:
            shot = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 55}, timeout=30)
            out["screenshot"] = shot.get("data", "")
        except BrowserError:
            out["screenshot"] = ""

        # Requests: anything still open counts as unfinished
        for rid, r in rec.requests.items():
            r.update(unfinished=True)
            rec.finished.append(r)
        t0 = min((r["start"] for r in rec.finished), default=0)
        reqs = []
        for r in rec.finished:
            reqs.append({"url": r["url"], "type": r.get("type", ""), "status": r.get("status", 0),
                         "size": r.get("size", 0), "mime": r.get("mime", ""), "protocol": r.get("protocol", ""),
                         "start_ms": (r["start"] - t0) * 1000.0,
                         "ms": ((r.get("end") or r["start"]) - r["start"]) * 1000.0,
                         "wait": r.get("wait"), "failed": r.get("failed", ""), "redirect": r.get("redirect", False),
                         "method": r.get("method", "GET"), "unfinished": r.get("unfinished", False),
                         "canceled": r.get("canceled", False), "initiator": r.get("initiator", "")})
        reqs.sort(key=lambda r: r["start_ms"])
        out["requests"] = reqs
        out["total_bytes"] = sum(r["size"] for r in reqs)
        out["backend_calls"] = [{"url": r["url"], "ms": r["ms"], "status": r["status"]} for r in reqs
                                if ("admin-ajax.php" in r["url"] or "/wp-json/" in r["url"] or "rest_route=" in r["url"])
                                and r["type"] in ("XHR", "Fetch", "Other", "")]
        wt = out.get("wt") or {}
        fcp = out.get("fcp") or 0
        out["lcp"] = (wt.get("lcp") or {}).get("t")
        out["lcp_info"] = wt.get("lcp") or {}
        out["cls"] = wt.get("cls", 0)
        out["shifts"] = wt.get("shifts", [])
        out["tbt"] = sum(max(0, d - 50) for s, d in wt.get("longtasks", []) if s + d > fcp)
        out["blocking_all"] = sum(max(0, d - 50) for s, d in wt.get("longtasks", []))
        out["longtasks"] = len(wt.get("longtasks", []))
        out.pop("wt", None)
        return out


def run(url, profiles=(DESKTOP, MOBILE), deep_profile="desktop", progress=None, cancelled=None):
    """Load the page once per profile, each in a fresh private context. Returns {profile_name: data or error}."""
    results = {}
    try:
        with Browser() as b:
            results["browser_name"] = b.name
            if progress:
                progress("Starting the browser")
            b.warm_up(url)
            for profile in profiles:
                if cancelled and cancelled():
                    break
                if progress:
                    progress(f"Loading the page in a real browser ({profile['name']})")
                try:
                    results[profile["name"]] = b.load(url, profile, deep=profile["name"] == deep_profile,
                                                      timeout=90 if profile["mobile"] else 60, cancelled=cancelled)
                except (BrowserError, OSError, websocket.WebSocketException) as e:
                    results[profile["name"]] = {"error": str(e)}
    except (BrowserError, OSError, websocket.WebSocketException) as e:
        for profile in profiles:
            results.setdefault(profile["name"], {"error": str(e)})
    return results
