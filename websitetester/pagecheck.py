"""The page itself: what it loads, how big it is, and what slows it down."""
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from . import net
from .findings import CRITICAL, GOOD, INFO, WARNING, Finding

MAX_ASSETS = 160
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".bmp", ".ico")
FONT_EXT = (".woff2", ".woff", ".ttf", ".otf", ".eot")
TEXT_TYPES = ("text/", "application/javascript", "application/x-javascript", "application/json", "image/svg+xml",
              "application/xml", "application/ld+json")
EMBED_HOSTS = {"youtube.com": "YouTube video", "youtube-nocookie.com": "YouTube video", "vimeo.com": "Vimeo video",
               "google.com/maps": "Google Map", "maps.google": "Google Map", "facebook.com": "Facebook widget",
               "instagram.com": "Instagram embed", "twitter.com": "Twitter/X embed", "calendly.com": "Calendly"}


def kb(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    return f"{n / 1024:.0f} KB"


def short_url(url, limit=90):
    url = re.sub(r"^https?://", "", url)
    return url if len(url) <= limit else url[:limit - 3] + "..."


class PageParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.in_head = True
        self.title = ""
        self.meta = {}
        self.lang = ""
        self.canonical = ""
        self.stylesheets = []
        self.scripts = []
        self.images = []
        self.iframes = []
        self.preconnects = []
        self.preloads = []
        self.icons = []
        self.h1_count = 0
        self.inline_script_bytes = 0
        self.inline_style_bytes = 0
        self.inline_scripts = 0
        self.comments = []
        self.links = []
        self._text_target = None

    def _abs(self, url):
        url = (url or "").strip()
        if not url or url.startswith(("data:", "javascript:", "#", "about:", "blob:", "mailto:", "tel:")):
            return ""
        return urljoin(self.base, url)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        elif tag == "body":
            self.in_head = False
        elif tag == "title" and not self.title:
            self._text_target = "title"
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or a.get("http-equiv") or "").lower()
            if key:
                self.meta.setdefault(key, a.get("content", ""))
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            href = self._abs(a.get("href"))
            if "stylesheet" in rel and href:
                self.stylesheets.append({"url": href, "media": a.get("media", "all").lower(), "in_head": self.in_head,
                                         "id": a.get("id", "")})
            elif "preconnect" in rel or "dns-prefetch" in rel:
                self.preconnects.append(href)
            elif "preload" in rel and href:
                self.preloads.append({"url": href, "as": a.get("as", "")})
            elif "canonical" in rel:
                self.canonical = href
            elif "icon" in rel and href:
                self.icons.append(href)
        elif tag == "script":
            src = self._abs(a.get("src"))
            kind = a.get("type", "").lower()
            if src:
                self.scripts.append({"url": src, "async": "async" in a, "defer": "defer" in a,
                                     "module": kind == "module", "in_head": self.in_head, "id": a.get("id", "")})
            elif kind in ("", "text/javascript", "module", "application/javascript"):
                self.inline_scripts += 1
                self._text_target = "script"
        elif tag == "style":
            self._text_target = "style"
        elif tag in ("img", "source"):
            lazy_src = a.get("data-src") or a.get("data-lazy-src") or a.get("data-lazysrc") or a.get("data-original")
            src = self._abs(lazy_src or a.get("src") or "")
            if not src and a.get("srcset"):
                src = self._abs(a["srcset"].split(",")[0].strip().split(" ")[0])
            if tag == "img":
                self.images.append({
                    "url": src, "loading": a.get("loading", "").lower(),
                    "lazy": a.get("loading", "").lower() == "lazy" or bool(lazy_src) or "lazy" in a.get("class", ""),
                    "has_size": bool(a.get("width")) and bool(a.get("height")),
                    "srcset": bool(a.get("srcset") or a.get("data-srcset")), "alt": "alt" in a,
                    "fetchpriority": a.get("fetchpriority", ""),
                })
        elif tag == "iframe":
            src = self._abs(a.get("src") or a.get("data-src"))
            if src:
                self.iframes.append({"url": src, "lazy": a.get("loading", "").lower() == "lazy" or "data-src" in a})
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "a" and a.get("href"):
            if len(self.links) < 500:
                self.links.append(self._abs(a["href"]))

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False
        if tag in ("title", "script", "style"):
            self._text_target = None

    def handle_data(self, data):
        if self._text_target == "title":
            self.title += data
        elif self._text_target == "script":
            self.inline_script_bytes += len(data)
        elif self._text_target == "style":
            self.inline_style_bytes += len(data)

    def handle_comment(self, data):
        if len(self.comments) < 200:
            self.comments.append(data.strip()[:300])


@dataclass
class Asset:
    url: str
    kind: str
    source: str = "html"
    blocking: bool = False
    resp: net.Response = None
    extra: dict = field(default_factory=dict)

    @property
    def transfer(self):
        if not self.resp:
            return 0
        return self.resp.transfer_size

    @property
    def size(self):
        return len(self.resp.body) if self.resp else 0

    @property
    def failed(self):
        return self.resp is None or bool(self.resp.error) or self.resp.status >= 400


def kind_of(url, hint=""):
    path = urlsplit(url).path.lower()
    if hint:
        return hint
    if path.endswith(".css"):
        return "css"
    if path.endswith((".js", ".mjs")):
        return "js"
    if path.endswith(IMAGE_EXT):
        return "image"
    if path.endswith(FONT_EXT):
        return "font"
    return "other"


def collect_assets(parser):
    seen = set()
    assets = []

    def add(url, kind, **kw):
        if url and url not in seen and len(assets) < MAX_ASSETS:
            seen.add(url)
            assets.append(Asset(url, kind, **kw))

    for s in parser.stylesheets:
        add(s["url"], "css", blocking=s["in_head"] and s["media"] in ("", "all", "screen"))
    for s in parser.scripts:
        add(s["url"], "js", blocking=s["in_head"] and not (s["async"] or s["defer"] or s["module"]))
    for p in parser.preloads:
        add(p["url"], {"style": "css", "script": "js", "font": "font", "image": "image"}.get(p["as"], kind_of(p["url"])))
    for img in parser.images:
        add(img["url"], "image")
    for icon in parser.icons[:2]:
        add(icon, "image")
    return assets


CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
CSS_IMPORT = re.compile(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", re.I)


def fetch_assets(assets, progress=None, workers=8, cancelled=None):
    """Download every file the page uses (and fonts/images the CSS uses). Fills in asset.resp."""
    def get(asset):
        if cancelled and cancelled():
            return asset
        asset.resp = net.fetch(asset.url, timeout=20.0, headers={"Accept": "*/*"})
        return asset
        asset.resp = net.fetch(asset.url, timeout=20.0, headers={"Accept": "*/*", "Referer": asset.extra.get("ref", "")})
        return asset

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for asset in pool.map(get, list(assets)):
            done += 1
            if progress:
                progress(done, len(assets))
    # Second round: things referenced from CSS (fonts, @imports, background images)
    seen = {a.url for a in assets}
    extra = []
    for a in assets:
        if a.kind != "css" or a.failed:
            continue
        css = a.resp.text
        a.extra["imports"] = [urljoin(a.url, u) for u in CSS_IMPORT.findall(css)]
        a.extra["font_display"] = "font-display" in css
        # Browsers only download the best font format they support (woff2), so count just those if there are any
        fonts = [urljoin(a.url, r.strip()) for r in CSS_URL.findall(css) if kind_of(r.strip()) == "font"]
        woff2 = [u for u in fonts if urlsplit(u).path.lower().endswith(".woff2")]
        for url in woff2 or fonts:
            if url not in seen and len(assets) + len(extra) < MAX_ASSETS:
                seen.add(url)
                extra.append(Asset(url, "font", source="css"))
        for url in a.extra["imports"]:
            if url not in seen:
                seen.add(url)
                extra.append(Asset(url, "css", source="css @import", blocking=a.blocking))
    if extra:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(get, extra))
        assets.extend(extra)
    return assets


def _max_age(cache_control):
    m = re.search(r"max-age\s*=\s*(\d+)", cache_control or "", re.I)
    return int(m.group(1)) if m else None


def analyze(page, parser, assets, site_host, error_category="server"):
    """Findings about page weight, images, scripts, styles, fonts, caching headers, third parties."""
    findings = []
    stats = {}
    ok_assets = [a for a in assets if not a.failed]
    html_transfer = page.transfer_size
    total = html_transfer + sum(a.transfer for a in ok_assets)
    stats.update(html_bytes=len(page.body), html_transfer=html_transfer, total_bytes=total,
                 requests=1 + len(assets))
    by_kind = {}
    for a in ok_assets:
        k = by_kind.setdefault(a.kind, [0, 0])
        k[0] += 1
        k[1] += a.transfer
    stats["by_kind"] = by_kind

    # Page weight
    if total > 4 * 1024 * 1024:
        findings.append(Finding("content", CRITICAL, f"The page is very heavy ({kb(total)})",
                                "Visitors have to download this much before the page is complete. On a mobile "
                                "connection that alone can take 5-10 seconds.",
                                "Shrink images (the biggest are listed below), and remove plugins/scripts you don't "
                                "need on this page. Aim for under 2 MB.", impact=int(total / 1024 / 2)))
    elif total > 2 * 1024 * 1024:
        findings.append(Finding("content", WARNING, f"The page is heavy ({kb(total)})",
                                "A fast page is usually under 2 MB.",
                                "Shrink the biggest images and remove scripts you don't need.",
                                impact=int(total / 1024 / 4)))
    else:
        findings.append(Finding("content", GOOD, f"Page size is reasonable ({kb(total)})"))

    n = len(assets) + 1
    if n > 120:
        findings.append(Finding("content", CRITICAL, f"Too many files ({n} requests)",
                                "Every file is another request. Pages with this many files are almost always slowed by "
                                "plugins that each add their own CSS/JS.",
                                "Use an optimisation plugin to combine/delay files, and disable plugins' assets on "
                                "pages that don't need them (e.g. with Perfmatters or Asset CleanUp).", impact=n * 5))
    elif n > 70:
        findings.append(Finding("content", WARNING, f"Lots of files ({n} requests)",
                                "Under 50 is a good target.",
                                "Remove unused plugins and combine or delay non-essential CSS/JS.", impact=n * 3))

    # Broken files
    broken = [a for a in assets if a.failed]
    if broken:
        findings.append(Finding(
            "content", WARNING if len(broken) < 5 else CRITICAL,
            f"{len(broken)} file{'s' if len(broken) != 1 else ''} the page asks for {'are' if len(broken) != 1 else 'is'} missing or broken",
            "The page still asks the server for these. Each one wastes a request and, on WordPress, a missing file "
            "often makes the server run the whole of WordPress just to show a 404 error - that's slow.",
            "Remove the references (often an old plugin, theme or deleted image) or put the files back.",
            impact=200 * len(broken),
            items=[f"{short_url(a.url)} - {a.resp.error if a.resp and a.resp.error else a.resp.status if a.resp else 'not fetched'}"
                   for a in broken[:25]]))

    # Images
    images = [a for a in ok_assets if a.kind == "image" or a.resp.content_type.startswith("image/")]
    big = sorted([a for a in images if a.transfer > 200 * 1024], key=lambda a: -a.transfer)
    stats["image_bytes"] = sum(a.transfer for a in images)
    if big:
        huge = any(a.transfer > 1024 * 1024 for a in big)
        findings.append(Finding(
            "content", CRITICAL if huge or len(big) > 4 else WARNING,
            f"{len(big)} large image{'s' if len(big) != 1 else ''} ({kb(sum(a.transfer for a in big))} in total)",
            "These images are much bigger than they need to be for a web page.",
            "Resize them to the size they are shown at (rarely more than 2000 px wide) and compress them. In WordPress, "
            "an image optimisation plugin (ShortPixel, Imagify, EWWW or Smush) can do this for every existing image "
            "and convert them to WebP.",
            impact=int(sum(a.transfer for a in big) / 1024 / 3),
            items=[f"{kb(a.transfer)} - {short_url(a.url)}" for a in big[:20]]))
    old_format = [a for a in images if a.resp.content_type in ("image/jpeg", "image/png", "image/gif")
                  and a.transfer > 40 * 1024]
    if old_format:
        saving = int(sum(a.transfer for a in old_format) * 0.3)
        findings.append(Finding(
            "content", WARNING, f"{len(old_format)} images could be in a modern format (WebP/AVIF)",
            f"WebP images are usually 25-35% smaller than JPEG/PNG for the same quality - about {kb(saving)} saved "
            "here.", "Turn on WebP conversion in your image optimisation plugin (or in your CDN).",
            impact=int(saving / 1024 / 3), items=[f"{kb(a.transfer)} - {short_url(a.url)}" for a in old_format[:15]]))
    html_imgs = [i for i in parser.images if i["url"]]
    lazy = [i for i in html_imgs if i["lazy"]]
    stats["images_in_html"] = len(html_imgs)
    if len(html_imgs) > 6 and not lazy:
        findings.append(Finding(
            "content", WARNING, "Images aren't lazy-loaded",
            f"All {len(html_imgs)} images download straight away, even ones far down the page that the visitor may "
            "never scroll to.", "Turn on lazy loading (built into WordPress 5.5+, and most optimisation plugins). Keep "
            "the main image at the top of the page NOT lazy-loaded.", impact=150))
    no_size = [i for i in html_imgs if not i["has_size"]]
    if len(no_size) > 3:
        findings.append(Finding(
            "content", INFO, f"{len(no_size)} images have no width/height set",
            "Without a size the browser doesn't know how much space to leave, so the page can jump around as "
            "images load (\"layout shift\").",
            "Add width and height to images (WordPress does this automatically for images added in the editor; "
            "page builders and themes sometimes don't).",
            items=[short_url(i["url"]) for i in no_size[:10]]))

    # Render-blocking files
    blocking_js = [a for a in assets if a.kind == "js" and a.blocking]
    blocking_css = [a for a in assets if a.kind == "css" and a.blocking]
    stats["blocking_js"], stats["blocking_css"] = len(blocking_js), len(blocking_css)
    if len(blocking_js) > 2:
        cost = sum(a.resp.timing.total for a in blocking_js if a.resp)
        findings.append(Finding(
            "content", WARNING, f"{len(blocking_js)} scripts block the page from showing",
            "These JavaScript files are loaded in the page <head> without 'defer' or 'async', so the browser stops "
            "and waits for each one before it can draw anything.",
            "Defer JavaScript: most optimisation plugins have a \"Load JavaScript deferred\" / \"Delay JavaScript\" "
            "option (WP Rocket, LiteSpeed Cache, Perfmatters, Autoptimize). Test the site afterwards.",
            impact=int(min(cost, 2000) / 2), items=[f"{kb(a.transfer)} - {short_url(a.url)}" for a in blocking_js[:20]]))
    if len(blocking_css) > 6:
        findings.append(Finding(
            "content", WARNING, f"{len(blocking_css)} separate CSS files block the page from showing",
            "The browser must download every stylesheet in the <head> before it shows anything.",
            "Combine CSS files, remove ones that plugins load on every page, or use \"critical CSS\" / \"remove "
            "unused CSS\" in an optimisation plugin.", impact=len(blocking_css) * 20,
            items=[f"{kb(a.transfer)} - {short_url(a.url)}" for a in blocking_css[:25]]))
    imports = [i for a in assets if a.kind == "css" for i in a.extra.get("imports", [])]
    if imports:
        findings.append(Finding("content", WARNING, "CSS uses @import",
                                "@import makes the browser download one stylesheet before it even knows about the "
                                "next - a chain of delays.",
                                "Load these stylesheets directly (often a child theme's style.css importing the parent "
                                "theme's - enqueue it properly instead).", impact=150,
                                items=[short_url(u) for u in imports[:10]]))

    # Compression
    uncompressed = [a for a in ok_assets if a.resp.transfer_size > 2048
                    and not a.resp.header("content-encoding")
                    and any(a.resp.content_type.startswith(t) for t in TEXT_TYPES)]
    if uncompressed:
        findings.append(Finding(
            "caching", WARNING, f"{len(uncompressed)} text files are sent uncompressed",
            f"CSS, JavaScript and SVG compress to about a quarter of their size. {kb(sum(a.transfer for a in uncompressed))}"
            " is being sent the long way.",
            "Turn on GZIP/Brotli compression on the server (cPanel: Optimize Website > Compress All Content; or "
            "LiteSpeed/WP Rocket do it for you; or Cloudflare compresses automatically).",
            impact=int(sum(a.transfer for a in uncompressed) * 0.7 / 1024 / 3),
            items=[f"{kb(a.transfer)} - {short_url(a.url)}" for a in uncompressed[:15]]))

    # Unminified
    unminified = []
    for a in ok_assets:
        if a.kind in ("js", "css") and a.size > 20 * 1024 and ".min." not in a.url:
            text = a.resp.body[:200000]
            lines = text.count(b"\n") + 1
            if len(text) / lines < 120 and text.count(b"  ") > len(text) / 200:
                unminified.append(a)
    if unminified:
        findings.append(Finding(
            "content", INFO, f"{len(unminified)} CSS/JS files look unminified",
            "Minifying strips spaces and comments, typically saving 10-20%.",
            "Turn on CSS/JS minification in your caching/optimisation plugin.",
            items=[f"{kb(a.size)} - {short_url(a.url)}" for a in unminified[:10]]))

    # Browser caching for static files
    no_cache = []
    for a in ok_assets:
        if a.kind not in ("css", "js", "image", "font"):
            continue
        if not net.same_site(net.host_of(a.url), site_host):
            continue  # can't change other people's servers
        cc = a.resp.header("cache-control").lower()
        age = _max_age(cc)
        if "no-store" in cc or "no-cache" in cc or (age is not None and age < 7 * 86400) or \
                (age is None and not a.resp.header("expires")):
            no_cache.append(a)
    if no_cache:
        findings.append(Finding(
            "caching", WARNING, f"{len(no_cache)} of your files don't tell browsers to keep a copy",
            "Without a long cache time, returning visitors download these files again on every visit.",
            "Set browser caching (\"Expires\" / Cache-Control max-age) of at least 30 days for images, CSS, JS and "
            "fonts. WP Rocket, LiteSpeed Cache, W3 Total Cache and Cloudflare can do this; or add expires rules to "
            ".htaccess.", impact=100,
            items=[f"{short_url(a.url)} ({a.resp.header('cache-control') or 'no cache header'})" for a in no_cache[:15]]))
    elif ok_assets:
        findings.append(Finding("caching", GOOD, "Browser caching is set on your files"))

    # Slow individual files
    slow = sorted([a for a in ok_assets if a.resp.timing.wait > 1000], key=lambda a: -a.resp.timing.wait)
    if slow:
        findings.append(Finding(
            "content", WARNING, f"{len(slow)} files were slow to start downloading (over 1 second)",
            "These files made the browser wait for the server. For your own files this points at a slow or busy "
            "server; for other sites' files it means that service is slow.",
            "If they're your files, see the Server section. If they're from another site, consider removing or "
            "delaying that service.", impact=int(slow[0].resp.timing.wait / 3),
            items=[f"{a.resp.timing.wait:.0f} ms - {short_url(a.url)}" for a in slow[:15]]))

    # Third parties
    third = {}
    for a in ok_assets:
        h = net.host_of(a.url)
        if not net.same_site(h, site_host):
            entry = third.setdefault(h, [0, 0])
            entry[0] += 1
            entry[1] += a.transfer
    stats["third_party"] = third
    if len(third) > 8:
        findings.append(Finding(
            "content", WARNING, f"Files from {len(third)} other websites",
            "Each extra website means another DNS lookup, connection and SSL handshake before its files can load.",
            "Remove services you don't need (old tracking codes, chat widgets, social feeds) and host fonts locally.",
            impact=len(third) * 30,
            items=[f"{h} - {c} file{'s' if c != 1 else ''}, {kb(b)}" for h, (c, b) in
                   sorted(third.items(), key=lambda x: -x[1][1])[:20]]))

    # Fonts
    fonts = [a for a in assets if a.kind == "font"]
    google_fonts = any("fonts.googleapis.com" in a.url for a in assets)
    if len(fonts) > 6:
        findings.append(Finding("content", WARNING, f"{len(fonts)} font files",
                                "Each font weight/style is a separate download that can hold up text appearing.",
                                "Use fewer font weights (most sites need 2-3) and one or two font families.",
                                impact=len(fonts) * 20, items=[short_url(a.url) for a in fonts[:15]]))
    if google_fonts:
        findings.append(Finding("content", INFO, "Fonts are loaded from Google Fonts",
                                "That's an extra connection to Google before text can show (and in the UK/EU it "
                                "shares visitors' IP addresses with Google).",
                                "Host the fonts on your own site - most themes and optimisation plugins have a "
                                "\"local Google Fonts\" option, or use the OMGF plugin."))
    css_no_display = [a for a in ok_assets if a.kind == "css" and "@font-face" in a.resp.text[:500000]
                      and not a.extra.get("font_display")]
    if css_no_display:
        findings.append(Finding("content", INFO, "Web fonts don't use font-display: swap",
                                "Text may stay invisible until the font has downloaded.",
                                "Add font-display: swap to @font-face rules (many optimisation plugins have a "
                                "setting for this).", items=[short_url(a.url) for a in css_no_display[:5]]))

    # Embeds
    embeds = []
    for f in parser.iframes:
        for key, label in EMBED_HOSTS.items():
            if key in f["url"]:
                embeds.append((label, f))
    if embeds:
        not_lazy = [e for e in embeds if not e[1]["lazy"]]
        findings.append(Finding(
            "content", WARNING if not_lazy else INFO, f"{len(embeds)} embedded video/map/widget{'s' if len(embeds) != 1 else ''}",
            "Each YouTube embed loads around 1 MB of Google's code even if nobody presses play; maps and social "
            "widgets are similar.",
            "Use a \"click to load\" preview instead (WP Rocket's \"Replace YouTube iframe with preview image\", "
            "the Lite YouTube Embed plugin, or loading=\"lazy\" on the iframe).", impact=300 * len(not_lazy),
            items=[f"{label}: {short_url(f['url'])}" for label, f in embeds[:10]]))

    # Duplicate libraries
    names = {}
    for a in assets:
        if a.kind == "js":
            base = urlsplit(a.url).path.rsplit("/", 1)[-1].lower()
            base = re.sub(r"[.-]?(min|slim)?\.js$", "", base)
            names.setdefault(base, []).append(a.url)
    dupes = {k: v for k, v in names.items() if len(v) > 1 and k}
    jquery_versions = [u for k, v in names.items() if re.fullmatch(r"jquery(-\d[\d.]*)?", k) for u in v]
    if len(jquery_versions) > 1:
        dupes["jquery"] = jquery_versions
    if dupes:
        findings.append(Finding(
            "content", WARNING, "The same script is loaded more than once",
            "Usually two plugins (or a plugin and the theme) each bring their own copy of the same library.",
            "Find the plugin/theme loading the second copy and turn that option off (many have a \"don't load "
            "library\" setting).", impact=100,
            items=[f"{k}: " + ", ".join(short_url(u, 60) for u in v[:4]) for k, v in list(dupes.items())[:10]]))

    # HTML size
    html_size = len(page.body)
    if html_size > 200 * 1024:
        findings.append(Finding(
            "content", WARNING, f"The HTML itself is very large ({kb(html_size)})",
            f"Of this, {kb(parser.inline_style_bytes)} is inline CSS and {kb(parser.inline_script_bytes)} inline "
            "JavaScript. Page builders often stuff the page with code like this.",
            "Check your page builder's performance settings (e.g. Elementor > Settings > Performance: \"Improved CSS "
            "loading\" / Divi > Theme Options > Performance) and avoid huge menus or sliders duplicated for mobile.",
            impact=int(html_size / 1024 / 2)))

    # Mixed content
    if page.url.startswith("https://"):
        insecure = [a for a in assets if a.url.startswith("http://")]
        if insecure:
            findings.append(Finding(
                "security", WARNING, f"{len(insecure)} files load over insecure http://",
                "Browsers block or warn about insecure files on a secure page (\"mixed content\"), so images or "
                "scripts may be missing for visitors.",
                "Change these links to https:// - in WordPress, the Better Search Replace plugin can fix old links in "
                "the database.", items=[short_url(a.url) for a in insecure[:15]]))
    return findings, stats


PHP_ERROR = re.compile(r"<b>(Warning|Notice|Deprecated|Fatal error|Parse error)</b>:\s*(.{0,240}?)\s+in\s+<b>([^<]+)</b>",
                       re.I | re.S)


def php_errors(html):
    """PHP errors printed into the page (a plugin or theme is crashing and showing it to visitors)."""
    out = []
    for kind, message, path in PHP_ERROR.findall(html)[:20]:
        message = re.sub(r"<[^>]+>", "", message).strip()
        out.append(f"{kind}: {message} in {path}")
    for marker in ("Error establishing a database connection", "There has been a critical error on this website"):
        if marker in html:
            out.append(marker)
    return out


def blocks_everything(robots_txt):
    """True if robots.txt has 'Disallow: /' in the group for all robots (User-agent: *)."""
    agents, in_rules = [], False
    for line in robots_txt.splitlines():
        line = line.split("#", 1)[0].strip()
        name, _, value = line.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value)
        elif name in ("disallow", "allow"):
            in_rules = True
            if name == "disallow" and value == "/" and "*" in agents:
                return True
    return False


def check_seo_and_headers(page, parser, base_url):
    """SEO basics and security headers. Makes a couple of extra requests (robots.txt, sitemap)."""
    findings = []
    data = {}
    title = " ".join(parser.title.split())
    data["title"] = title
    if not title:
        findings.append(Finding("seo", WARNING, "The page has no title",
                                "The title is what shows in Google results and browser tabs.",
                                "Set an SEO title (in WordPress: with Yoast SEO / Rank Math, or Settings > General)."))
    elif len(title) > 65:
        findings.append(Finding("seo", INFO, f"The page title is long ({len(title)} characters)",
                                f"Google usually cuts titles off around 60 characters: \"{title[:80]}\""))
    else:
        findings.append(Finding("seo", GOOD, "Page title is set", items=[title]))
    desc = parser.meta.get("description", "").strip()
    if not desc:
        findings.append(Finding("seo", INFO, "No meta description",
                                "Google will pick some text from the page for the search result snippet instead.",
                                "Write a 1-2 sentence description in your SEO plugin."))
    if not parser.meta.get("viewport"):
        findings.append(Finding("seo", CRITICAL, "No mobile viewport tag",
                                "Phones will show a zoomed-out desktop version of the page, and Google ranks it as "
                                "not mobile-friendly.",
                                "Add <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"> (any "
                                "modern theme does this - an old or broken theme may not)."))
    robots_meta = (parser.meta.get("robots", "") + " " + page.header("x-robots-tag")).lower()
    if "noindex" in robots_meta:
        findings.append(Finding("seo", CRITICAL, "The page tells Google NOT to list it (noindex)",
                                "This page won't appear in search results.",
                                "In WordPress check Settings > Reading > \"Discourage search engines from indexing "
                                "this site\" is UNticked, and check the page's SEO plugin settings.", impact=1))
    if parser.h1_count == 0:
        findings.append(Finding("seo", INFO, "No main heading (H1) on the page",
                                "A single H1 heading helps search engines and screen readers understand the page."))
    if not parser.lang:
        findings.append(Finding("seo", INFO, "The page doesn't say what language it's in",
                                "Add lang=\"en-GB\" (or similar) to the <html> tag - your theme normally does this."))

    robots = net.fetch(urljoin(base_url, "/robots.txt"), timeout=15)
    data["robots_status"] = robots.status
    if robots.ok and "text" in robots.content_type:
        text = robots.text
        if blocks_everything(text):
            findings.append(Finding("seo", CRITICAL, "robots.txt blocks search engines from the whole site",
                                    "robots.txt contains \"Disallow: /\" for all robots, so Google won't crawl the site.",
                                    "Remove that line from robots.txt (WordPress: Settings > Reading > untick "
                                    "\"Discourage search engines\").", impact=1))
        sitemaps = re.findall(r"^\s*sitemap:\s*(\S+)", text, re.I | re.M)
        data["sitemaps"] = sitemaps
    sitemap_found = bool(data.get("sitemaps"))
    if not sitemap_found:
        for path in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml"):
            r = net.fetch(urljoin(base_url, path), timeout=15)
            if r.ok and b"<" in r.body[:200]:
                sitemap_found = True
                data["sitemaps"] = [urljoin(base_url, path)]
                break
    if not sitemap_found:
        findings.append(Finding("seo", INFO, "No XML sitemap found",
                                "A sitemap helps Google find all your pages.",
                                "Yoast SEO, Rank Math and WordPress itself (wp-sitemap.xml) can make one; then add it "
                                "in Google Search Console."))

    # Security headers
    h = page.headers
    missing = []
    if page.url.startswith("https://") and "strict-transport-security" not in h:
        missing.append("Strict-Transport-Security (forces HTTPS)")
    if "x-content-type-options" not in h:
        missing.append("X-Content-Type-Options: nosniff")
    if "x-frame-options" not in h and "frame-ancestors" not in h.get("content-security-policy", ""):
        missing.append("X-Frame-Options (stops your site being shown inside other sites)")
    if "referrer-policy" not in h:
        missing.append("Referrer-Policy")
    if missing:
        findings.append(Finding(
            "security", WARNING if len(missing) >= 3 else INFO, f"{len(missing)} security headers missing",
            "These small server settings protect visitors against some common attacks.",
            "Add them in .htaccess / your host's control panel, Cloudflare, or with a plugin such as \"Headers "
            "Security Advanced & HSTS WP\".", items=missing))
    else:
        findings.append(Finding("security", GOOD, "Security headers are set"))
    server = h.get("server", "")
    if re.search(r"/\d", server):
        findings.append(Finding("security", INFO, "The server announces its exact software version",
                                f"Server: {server}. This helps attackers look up known weaknesses.",
                                "Ask your host to hide it (Apache: ServerTokens Prod)."))
    return findings, data
