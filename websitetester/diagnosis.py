"""Turn all the measurements into a plain-English answer: what is slow, and why."""
from urllib.parse import urlsplit

from .findings import CRITICAL, GOOD, INFO, WARNING, Finding
from .pagecheck import kb, short_url
from .wordpress import PLUGIN_NAMES, owner_of


def secs(ms):
    if ms is None:
        return "-"
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


def _rate(value, good, poor):
    if value is None:
        return None
    return GOOD if value <= good else WARNING if value <= poor else CRITICAL


def browser_findings(results, known_broken=(), site_host=""):
    findings = []
    if not results:
        return findings
    for name in ("mobile", "desktop"):
        d = results.get(name)
        if not d:
            continue
        label = "phone" if name == "mobile" else "desktop"
        if d.get("error"):
            findings.append(Finding("browser", INFO, f"Real browser test ({label}) didn't run", d["error"]))
            continue
        lcp, fcp, cls, tbt = d.get("lcp"), d.get("fcp"), d.get("cls") or 0, d.get("tbt") or 0
        info = d.get("lcp_info") or {}
        what = ""
        if info.get("url"):
            what = f"The main content is the image {short_url(info['url'], 80)}."
        elif info.get("text"):
            what = f"The main content is the text \"{info['text'][:60]}\"."
        elif info.get("el"):
            what = f"The main content is <{info['el']}>."
        sev = _rate(lcp, 2500, 4000)
        if sev:
            slow_bit = "" if sev == GOOD else " Google wants this under 2.5 seconds."
            conditions = " (simulated mid-range phone on 4G)" if name == "mobile" else ""
            findings.append(Finding(
                "browser", sev, f"Main content appears after {secs(lcp)} on a {label}{conditions}",
                f"This is \"Largest Contentful Paint\" - when the biggest thing on the screen has loaded.{slow_bit} "
                f"{what} First content appeared at {secs(fcp)}.",
                "" if sev == GOOD else "See the diagnosis at the top for what's holding it up.",
                impact=int(max(0, lcp - 2500) / (1 if name == "mobile" else 1.5)), symptom=True))
        if info.get("lazy"):
            findings.append(Finding(
                "browser", WARNING, "The main image is lazy-loaded",
                "The biggest image at the top of the page waits until the browser has worked out the layout, which "
                "delays the moment the page looks ready.",
                "Exclude the first/hero image from lazy loading (most optimisation plugins have an \"exclude above "
                "the fold images\" setting) and give it fetchpriority=\"high\".", impact=300))
        sev = _rate(cls, 0.1, 0.25)
        if sev and sev != GOOD:
            movers = []
            for shift in d.get("shifts", []):
                for el in shift.get("els", []):
                    if el not in movers:
                        movers.append(el)
            findings.append(Finding(
                "browser", sev, f"The page jumps around while loading ({label}, CLS {cls:.2f})",
                "Things move after they first appear, so visitors lose their place or tap the wrong thing. Google "
                "wants under 0.1.",
                "Give images, videos, adverts and embeds a fixed width/height, and avoid banners/cookie bars that push "
                "the page down (overlay them instead). Fonts loading late can also cause this.",
                impact=int(cls * 1000), items=movers[:10]))
        elif sev == GOOD and name == "mobile":
            findings.append(Finding("browser", GOOD, f"The page doesn't jump around (CLS {cls:.2f})"))
        sev = _rate(tbt, 200, 600)
        if sev and sev != GOOD:
            findings.append(Finding(
                "browser", sev, f"The page freezes for {secs(tbt)} while loading ({label})",
                "\"Total Blocking Time\": JavaScript keeps the browser busy, so taps, clicks and scrolling don't "
                "respond. Google wants under 200 ms.",
                "Remove unnecessary JavaScript (sliders, animations, chat widgets, tracking) and use \"delay "
                "JavaScript execution\" in an optimisation plugin. See which plugins use the most JavaScript below.",
                impact=int(tbt)))
        if d.get("load_timed_out"):
            findings.append(Finding("browser", WARNING, f"The page never finished loading ({label})",
                                    "The browser was still waiting after a full minute - something on the page "
                                    "keeps loading or hangs.", impact=1000))
    d = results.get("desktop") if not (results.get("desktop") or {}).get("error") else results.get("mobile")
    if not d or d.get("error"):
        return findings

    errors = d.get("console") or []
    if errors:
        findings.append(Finding(
            "browser", WARNING, f"{len(errors)} JavaScript error{'s' if len(errors) != 1 else ''} on the page",
            "Errors in one script can stop others from working (menus, forms, sliders, tracking). They usually come "
            "from an outdated plugin or a plugin conflict.",
            "Update plugins and the theme. The address in each error shows which plugin it came from.",
            items=[f"{e['text'][:200]}" + (f" ({short_url(e['url'], 70)})" if e.get("url") else "") for e in errors[:12]]))
    backend = {c["url"] for c in d.get("backend_calls") or []}  # reported separately
    failed = [r for r in d.get("requests", []) if (r["status"] >= 400 or (r["failed"] and not r["canceled"]))
              and r["url"] not in known_broken and r["url"] not in backend and not r["url"].startswith("data:")]
    if failed:
        findings.append(Finding(
            "browser", WARNING, f"{len(failed)} more files failed to load in the browser",
            "These are files requested by scripts or styles (not directly in the page HTML).",
            "Remove the references, or put the missing files back.",
            items=[f"{r['status'] or r['failed']} - {short_url(r['url'])}" for r in failed[:15]]))
    dom = d.get("dom") or 0
    if dom > 3000:
        findings.append(Finding("content", CRITICAL, f"Huge page structure ({dom:,} HTML elements)",
                                "Huge pages take longer to lay out and use more memory - common with page builders "
                                "(sections inside columns inside rows...) and big mega-menus.",
                                "Simplify the page layout; avoid nesting sections; remove duplicated mobile/desktop "
                                "versions of the same content.", impact=dom // 10))
    elif dom > 1500:
        findings.append(Finding("content", WARNING, f"Large page structure ({dom:,} HTML elements)",
                                "Google recommends under 1,500 elements.",
                                "Simplify page builder layouts and menus.", impact=dom // 20))

    # Images much bigger than shown
    sizes = {r["url"]: r["size"] for r in d.get("requests", [])}
    dpr = d.get("dpr") or 1
    oversized = []
    for img in d.get("imgs") or []:
        if not img.get("visible") or not img["w"] or not img["nw"]:
            continue
        need_w, need_h = img["w"] * dpr * 1.5, img["h"] * dpr * 1.5
        size = sizes.get(img["src"], 0)
        if img["nw"] > need_w and img["nh"] > need_h and size > 60 * 1024:
            oversized.append((size, img))
    oversized.sort(key=lambda x: -x[0])
    if oversized:
        findings.append(Finding(
            "content", WARNING, f"{len(oversized)} images are much bigger than the space they're shown in",
            "The browser downloads a large image and then shrinks it down. That's wasted download time.",
            "Upload images at the size they're displayed (or let WordPress pick the right size with srcset - avoid "
            "\"Full size\" in the editor and page builders).",
            impact=int(sum(s for s, _ in oversized) * 0.6 / 1024 / 3),
            items=[f"{short_url(i['src'], 70)}: {i['nw']}x{i['nh']} px shown at {i['w']}x{i['h']} ({kb(s)})"
                   for s, i in oversized[:12]]))
    script_s = (d.get("metrics") or {}).get("ScriptDuration")
    if script_s and script_s > 1.0:
        findings.append(Finding("browser", WARNING, f"{script_s:.1f} s of JavaScript running on a desktop PC",
                                "On a phone this is 3-5 times longer.",
                                "See \"Who uses the processor\" in the Plugins section.", impact=int(script_s * 300)))
    return findings


def breakdown(r):
    """Where the time goes for a first-time visitor (desktop, from this PC). List of (label, ms, key)."""
    s = r.server or {}
    home = s.get("home") or {}
    rows = []
    dns_ms = (r.dns or {}).get("auth_ms")
    if dns_ms is None and r.page:
        dns_ms = r.page.timing.dns
    rows.append(("Finding the server (DNS)", dns_ms or 0, "dns"))
    redirect_ms = (r.redirects or {}).get("redirect_ms") or 0
    if redirect_ms:
        rows.append(("Redirects", redirect_ms, "redirect"))
    rows.append(("Connecting", home.get("rtt") or 0, "network"))
    if home.get("tls"):
        rows.append(("Secure connection (SSL)", home["tls"], "network"))
    rows.append(("Request travelling to the server and back", home.get("rtt") or 0, "network"))
    rows.append(("Server preparing the page", home.get("think") or 0, "server"))
    rows.append(("Downloading the page", home.get("download") or 0, "network"))
    d = (r.browser or {}).get("desktop") or {}
    if d and not d.get("error") and d.get("lcp"):
        nav = d.get("nav") or {}
        rest = d["lcp"] - (nav.get("responseEnd") or 0)
        rows.append(("Loading images, CSS & JavaScript and drawing the page", max(0, rest), "frontend"))
    return rows


def _top_weights(r, limit=4):
    """Biggest contributors to page weight / JS, by owner (plugin, theme, images, other sites)."""
    groups = {}
    for a in r.assets or []:
        if a.failed:
            continue
        kind, slug = owner_of(a.url, r.site_host)
        if kind == "plugin":
            name = "the " + (((r.wp or {}).get("plugins") or {}).get(slug, {}).get("name")
                             or PLUGIN_NAMES.get(slug, slug)) + " plugin"
        elif kind == "theme":
            name = f"the theme ({slug})"
        elif kind == "uploads" or a.kind == "image":
            name = "images"
        elif kind == "other site":
            name = "other sites' files (tracking, fonts, widgets)"
        elif kind == "core":
            name = "WordPress core files"
        else:
            name = "your own files"
        groups[name] = groups.get(name, 0) + a.transfer
    return sorted(groups.items(), key=lambda kv: -kv[1])[:limit]


def diagnose(r):
    """Returns {'headline', 'paragraphs': [...], 'causes': [(area, ms, text)]}."""
    s = r.server or {}
    home = s.get("home") or {}
    think = home.get("think")
    rtt = home.get("rtt") or 0
    static = s.get("static_think")
    wp_think = s.get("wp_think")
    uncached = s.get("uncached_think")
    page_cache = s.get("page_cache")
    causes = []

    if think is not None and think > 250:
        if static is not None and static > 300:
            causes.append(("Hosting / server", think,
                           f"The server is slow. It takes {secs(think)} to start sending the page, and even a plain "
                           f"file with no WordPress involved takes {secs(static)} - so the server itself is overloaded "
                           "or underpowered. This is a hosting problem: no plugin will fix it. Upgrade the hosting "
                           "plan or move to a better host."))
        elif r.is_wp and not page_cache:
            extra = ""
            if wp_think is not None:
                extra = (f" Just starting WordPress and its plugins takes {secs(wp_think)}"
                         + (f", while a plain file comes back in {secs(static)} - so the server is fine, it's the "
                            "WordPress side (plugins, theme, database)." if static is not None else "."))
            causes.append(("WordPress (no page cache)", think,
                           f"WordPress builds every page from scratch for every visitor: the server needs {secs(think)} "
                           f"before it starts sending the page.{extra} Add a page caching plugin first - that alone "
                           "usually brings this under 100 ms - then trim slow plugins."))
        elif r.is_wp:
            causes.append(("WordPress / plugins", think,
                           f"The server needs {secs(think)} to start sending the page"
                           + (f" (a plain file takes {secs(static)}, so the server hardware is OK)" if static is not None else "")
                           + ". The page cache isn't helping this page enough - check it's switched on for this page "
                           "and that it isn't being bypassed (cookies, query strings, a plugin turning it off). Also "
                           "look at slow plugins in the Plugins section."))
        else:
            causes.append(("Server", think, f"The server needs {secs(think)} to start sending the page. Check "
                                            "the website software's caching and the hosting plan."))
    elif r.is_wp and uncached and uncached > 800:
        causes.append(("WordPress behind the cache", uncached * 0.3,
                       f"Cached pages are fast, but without the cache WordPress needs {secs(uncached)}. First visitors, "
                       "logged-in users and shoppers get the slow version."))
    if rtt > 100:
        causes.append(("Distance to the server", rtt * 4,
                       f"The server is far away: each round trip takes {secs(rtt)} and a new visitor needs about four "
                       f"before the page starts arriving (about {secs(rtt * 4)})."
                       + (f" It's in {s['server_location']}." if s.get("server_location") else "")
                       + " A CDN such as Cloudflare fixes most of this."))
    dns_ms = (r.dns or {}).get("auth_ms")
    if dns_ms and dns_ms > 100:
        causes.append(("DNS", dns_ms, f"Looking up the domain is slow ({secs(dns_ms)}) - "
                                      f"{(r.dns or {}).get('provider') or 'the DNS provider'} is slow to answer. "
                                      "Moving DNS to Cloudflare (free) usually gets this under 30 ms."))
    redirect_ms = (r.redirects or {}).get("redirect_ms") or 0
    if redirect_ms > 250:
        causes.append(("Redirects", redirect_ms, f"Redirects cost {secs(redirect_ms)} before the page even starts."))

    mobile = (r.browser or {}).get("mobile") or {}
    desktop = (r.browser or {}).get("desktop") or {}
    m_lcp = mobile.get("lcp") if not mobile.get("error") else None
    d_lcp = desktop.get("lcp") if not desktop.get("error") else None
    if m_lcp:
        front = m_lcp - ((mobile.get("nav") or {}).get("responseEnd") or 0)
        if front > 1500:
            weights = _top_weights(r)
            parts = ", ".join(f"{name} ({kb(size)})" for name, size in weights if size > 30 * 1024)
            tbt = mobile.get("tbt") or 0
            causes.append(("Page content (images, CSS, JavaScript)", front / 2,
                           f"Once the page arrives, a phone needs another {secs(front)} to download and run everything "
                           f"before the main content shows. The page is {kb(mobile.get('total_bytes') or 0)} in "
                           f"{len(mobile.get('requests') or [])} files"
                           + (f", with {secs(tbt)} of JavaScript freezing the phone" if tbt > 200 else "")
                           + "." + (f" The biggest parts are {parts}." if parts else "")))

    causes.sort(key=lambda c: -c[1])
    if r.page is None or r.page.error:
        reason = r.page.error if r.page is not None else "it couldn't be reached"
        return {"headline": "The website couldn't be reached",
                "paragraphs": [f"{r.start_url}: {reason}. Check the domain hasn't expired, its DNS points at the right "
                               "server, and the server is running."], "causes": []}
    paragraphs = []
    speed = []
    if m_lcp:
        speed.append(f"on a phone the main content appears after {secs(m_lcp)}")
    if d_lcp:
        speed.append(f"on a desktop PC after {secs(d_lcp)}")
    if speed:
        paragraphs.append("Measured in a real browser: " + " and ".join(speed) + ". Google's target is 2.5 s.")
    if not causes:
        headline = "No major speed problems found"
        if think is not None:
            paragraphs.append(f"The server answers quickly ({secs(think)}), and the page loads well. See the details "
                              "below for smaller improvements.")
    else:
        area, _, text = causes[0]
        headline = f"Main problem: {area}"
        paragraphs.append(text)
        for area, _, text in causes[1:4]:
            paragraphs.append(f"Also - {area}: {text}")
    return {"headline": headline, "paragraphs": paragraphs, "causes": causes}


def where_is_the_problem(findings):
    """Short traffic-light list for the summary: area -> (status, worst finding title)."""
    areas = [("Server / hosting", ("server",)), ("DNS", ("dns",)), ("SSL", ("ssl",)), ("Caching & CDN", ("caching",)),
             ("WordPress", ("wordpress",)), ("Plugins & theme", ("plugins",)),
             ("Images, CSS & JavaScript", ("content", "browser")), ("Security", ("security",)),
             ("SEO", ("seo",)), ("Email setup", ("email",))]
    out = []
    for label, cats in areas:
        mine = [f for f in findings if f.category in cats]
        if not mine:
            continue
        causes = [f for f in mine if not f.symptom and f.severity in (CRITICAL, WARNING)]
        mine = causes or mine
        worst = min(mine, key=lambda f: f.sort_key)
        status = worst.severity if worst.severity in (CRITICAL, WARNING) else GOOD
        out.append((label, status, worst.title if status != GOOD else "No problems found"))
    return out


def domain_label(url):
    return urlsplit(url).hostname or url
