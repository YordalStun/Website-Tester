"""The report: a standalone HTML page (opened in the browser) and a short summary for the app window."""
from html import escape

from . import APP_NAME, VERSION
from .diagnosis import secs, where_is_the_problem
from .findings import (CATEGORIES, CRITICAL, GOOD, INFO, SEVERITY_LABEL, WARNING, category_score, grade,
                       top_fixes)
from .pagecheck import kb, short_url
from .wordpress import is_older, owner_of

COLORS = {CRITICAL: "#d93025", WARNING: "#e37400", INFO: "#5f6368", GOOD: "#188038"}
ICONS = {CRITICAL: "&#10006;", WARNING: "&#9888;", INFO: "&#8505;", GOOD: "&#10004;"}
GRADE_COLORS = {"A": "#188038", "B": "#4c8c2b", "C": "#b08000", "D": "#e37400", "E": "#d93025", "F": "#b31412",
                "?": "#5f6368"}
BAR_COLORS = {"dns": "#8e63ce", "redirect": "#c5221f", "network": "#1a73e8", "server": "#e37400",
              "frontend": "#12a4af"}

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1f2329;--muted:#5f6368;--line:#e3e6ea;--soft:#f1f3f4;--accent:#1a73e8}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--card:#1f2228;--text:#e8eaed;--muted:#9aa0a6;--line:#33373e;
--soft:#2a2e35;--accent:#8ab4f8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 "Segoe UI",system-ui,-apple-system,Roboto,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 16px 60px}
header.top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:baseline;margin-bottom:16px}
header.top h1{font-size:22px;margin:0}
header.top .meta{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:16px 0}
h2{font-size:19px;margin:0 0 12px}
h3{font-size:16px;margin:18px 0 8px}
.hero{display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap}
.grade{width:110px;height:110px;border-radius:50%;display:flex;flex-direction:column;align-items:center;
justify-content:center;color:#fff;flex:none}
.grade b{font-size:46px;line-height:1}
.grade span{font-size:13px;opacity:.9}
.hero .text{flex:1;min-width:260px}
.hero h2{font-size:22px}
.hero p{margin:8px 0}
.areas{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.area{border:1px solid var(--line);border-radius:10px;padding:10px 12px;border-left-width:5px}
.area b{display:block}
.area small{color:var(--muted)}
.bar{display:flex;height:28px;border-radius:6px;overflow:hidden;margin:10px 0 6px;background:var(--soft)}
.bar div{min-width:2px}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:13px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:13px}
td.num,th.num{text-align:right;white-space:nowrap}
.tablewrap{overflow-x:auto}
ol.fixes{padding-left:22px;margin:0}
ol.fixes li{margin:0 0 14px}
.finding{border-top:1px solid var(--line);padding:12px 0}
.finding:first-of-type{border-top:0}
.ftitle{font-weight:600;display:flex;gap:8px;align-items:baseline}
.pill{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;padding:2px 7px;border-radius:20px;
color:#fff;white-space:nowrap;flex:none}
.detail{margin:4px 0 0;color:var(--text)}
.fix{margin:6px 0 0;padding:8px 10px;background:var(--soft);border-radius:8px}
.fix b{color:var(--accent)}
details{margin-top:6px}
summary{cursor:pointer;color:var(--accent);font-size:14px}
details ul{margin:6px 0 0;padding-left:20px;font-size:13px;word-break:break-word}
.score{float:right;font-size:14px;font-weight:600;padding:2px 10px;border-radius:20px;color:#fff}
.shots{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
.shots figure{margin:0}
.shots img{border:1px solid var(--line);border-radius:8px;max-width:100%;display:block}
.shots figcaption{font-size:13px;color:var(--muted);margin-top:4px}
.kv td:first-child{color:var(--muted);width:38%}
.muted{color:var(--muted)}
.small{font-size:13px}
code{font-size:13px;background:var(--soft);padding:1px 5px;border-radius:4px;word-break:break-all}
@media print{body{background:#fff}.card{break-inside:avoid;border-color:#ccc}details{display:block}}
"""


def e(text):
    return escape(str(text if text is not None else ""))


def pill(sev):
    return f'<span class="pill" style="background:{COLORS[sev]}">{SEVERITY_LABEL[sev]}</span>'


def finding_html(f, show_fix=True):
    out = [f'<div class="finding"><div class="ftitle">{pill(f.severity)}<span>{e(f.title)}</span></div>']
    if f.detail:
        out.append(f'<p class="detail">{e(f.detail)}</p>')
    if show_fix and f.fix:
        out.append(f'<p class="fix"><b>How to fix:</b> {e(f.fix)}</p>')
    if f.items:
        out.append(f"<details><summary>Show details ({len(f.items)})</summary><ul>"
                   + "".join(f"<li>{e(i)}</li>" for i in f.items) + "</ul></details>")
    out.append("</div>")
    return "".join(out)


def score_color(score):
    return GRADE_COLORS[grade(score)]


def _metric_row(label, desktop, mobile, google=""):
    return f"<tr><td>{e(label)}</td><td class=num>{desktop}</td><td class=num>{mobile}</td><td class=num>{google}</td></tr>"


def _rate_span(value, good, poor, text):
    if value is None:
        return "-"
    sev = GOOD if value <= good else WARNING if value <= poor else CRITICAL
    return f'<span style="color:{COLORS[sev]};font-weight:600">{e(text)}</span>'


def browser_section(r):
    b = r.browser or {}
    d, m = b.get("desktop") or {}, b.get("mobile") or {}
    if not d and not m:
        return ""
    psi = r.psi or {}
    lab = psi.get("lab") or {}

    def val(data, key, good, poor, fmt):
        if not data or data.get("error") or data.get(key) is None:
            return "-"
        v = data.get(key)
        return _rate_span(v, good, poor, fmt(v))
    rows = [
        _metric_row("Main content appears (LCP)", val(d, "lcp", 2500, 4000, secs), val(m, "lcp", 2500, 4000, secs),
                    e(lab.get("largest-contentful-paint", ""))),
        _metric_row("First content appears (FCP)", val(d, "fcp", 1800, 3000, secs), val(m, "fcp", 1800, 3000, secs),
                    e(lab.get("first-contentful-paint", ""))),
        _metric_row("Page freezes (Total Blocking Time)", val(d, "tbt", 200, 600, secs), val(m, "tbt", 200, 600, secs),
                    e(lab.get("total-blocking-time", ""))),
        _metric_row("Page jumping around (CLS)", val(d, "cls", 0.1, 0.25, lambda v: f"{v:.2f}"),
                    val(m, "cls", 0.1, 0.25, lambda v: f"{v:.2f}"), e(lab.get("cumulative-layout-shift", ""))),
    ]
    for label, key in (("Files downloaded", "requests"), ("Total downloaded", "total_bytes"), ("HTML elements", "dom")):
        def plain(data):
            if not data or data.get("error"):
                return "-"
            v = data.get(key)
            if key == "requests":
                return str(len(v or []))
            if key == "total_bytes":
                return kb(v or 0)
            return f"{v or 0:,}"
        rows.append(_metric_row(label, plain(d), plain(m)))
    head = "<tr><th></th><th class=num>Desktop PC</th><th class=num>Phone (4G)</th><th class=num>Google test</th></tr>"
    shots = []
    for data, label, width in ((d, "Desktop", 420), (m, "Phone", 200)):
        if data.get("screenshot"):
            shots.append(f'<figure><img alt="{label} screenshot" width="{width}" '
                         f'src="data:image/jpeg;base64,{data["screenshot"]}"><figcaption>{label}, when loading had '
                         f'finished</figcaption></figure>')
    errors = [f"{name}: {data['error']}" for name, data in (("Desktop", d), ("Phone", m)) if data.get("error")]
    return (f'<section class="card"><h2>What visitors see (real browser test)</h2>'
            f'<p class="muted small">The page was loaded in {e(b.get("browser_name") or "a browser")} like a first-time '
            "visitor (empty cache). The phone test simulates a mid-range phone on a 4G connection, like Google does. "
            "Green = good, orange = needs improvement, red = poor.</p>"
            + "".join(f'<p style="color:{COLORS[WARNING]}">{e(x)}</p>' for x in errors)
            + f'<div class="tablewrap"><table>{head}{"".join(rows)}</table></div>'
            + (f'<div class="shots" style="margin-top:14px">{"".join(shots)}</div>' if shots else "")
            + "</section>")


def breakdown_section(r):
    rows = [(label, ms, key) for label, ms, key in r.breakdown if ms is not None]
    total = sum(ms for _, ms, _ in rows)
    if total <= 0:
        return ""
    bar = "".join(f'<div title="{e(label)}: {secs(ms)}" style="width:{ms * 100 / total:.2f}%;background:{BAR_COLORS[key]}"></div>'
                  for label, ms, key in rows if ms > 0)
    table = "".join(f'<tr><td><i style="display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;'
                    f'background:{BAR_COLORS[key]}"></i>{e(label)}</td><td class=num>{secs(ms)}</td></tr>'
                    for label, ms, key in rows)
    return (f'<section class="card"><h2>Where the time goes</h2>'
            f'<p class="muted small">A first-time visitor on a desktop PC, from where this test ran '
            f'({e((r.server or {}).get("tester_location") or "this PC")}). Total about {secs(total)} until the main content '
            "shows.</p>"
            f'<div class="bar">{bar}</div>'
            f'<div class="legend"><span><i style="background:{BAR_COLORS["dns"]}"></i>DNS</span>'
            f'<span><i style="background:{BAR_COLORS["redirect"]}"></i>Redirects</span>'
            f'<span><i style="background:{BAR_COLORS["network"]}"></i>Network / distance</span>'
            f'<span><i style="background:{BAR_COLORS["server"]}"></i>Server / WordPress</span>'
            f'<span><i style="background:{BAR_COLORS["frontend"]}"></i>Page content &amp; browser</span></div>'
            f'<div class="tablewrap"><table style="margin-top:10px">{table}</table></div></section>')


def server_section(r):
    s = r.server or {}
    if not s:
        return ""
    rows = []

    def row(label, value):
        if value not in (None, "", []):
            rows.append(f"<tr><td>{e(label)}</td><td>{e(value)}</td></tr>")
    row("IP address", s.get("ip"))
    row("Server name (reverse DNS)", s.get("reverse_dns"))
    row("Network / hosting company", s.get("server_org"))
    row("Hosting (from headers)", s.get("host_hint"))
    row("Location", s.get("server_location"))
    row("Tested from", s.get("tester_location"))
    if s.get("distance_km") is not None:
        row("Distance", f"about {s['distance_km']:,.0f} km")
    row("Web server software", s.get("server_header"))
    row("PHP version", s.get("php"))
    row("CDN", s.get("cdn") or "none detected")
    row("Page cache", "yes" + (f" ({s.get('cache_header')})" if s.get("cache_header") else "") if s.get("page_cache") else "not detected")
    row("Compression", s.get("compression"))
    if "http2" in s:
        row("HTTP/2", "yes" if s["http2"] else "no")
        row("HTTP/3", "yes" if s.get("http3") else "no")
    ssl = r.ssl or {}
    cert = ssl.get("cert") or {}
    row("TLS version", ssl.get("tls_version"))
    if cert:
        row("SSL certificate", f"{cert.get('issuer')}, expires {cert.get('expires')} ({cert.get('days_left')} days)")

    timing = []
    for label, key in (("Home page (normal visit)", "home"), ("Home page, cache skipped", "uncached"),
                       ("A plain file (no WordPress)", "static"), ("WordPress start-up (admin-ajax.php)", "wp_backend")):
        t = s.get(key)
        if t:
            timing.append(f"<tr><td>{e(label)}</td><td class=num>{secs(t['rtt'])}</td><td class=num>{secs(t['think'])}</td>"
                          f"<td class=num>{secs(t['think_min'])} - {secs(t['think_max'])}</td>"
                          f"<td class=num>{t['count']}</td><td>{e(', '.join(c for c in t.get('cache', []) if c) or '-')}</td></tr>")
    timing_html = ""
    if timing:
        timing_html = ("<h3>Server timing</h3><p class='muted small'>\"Server work\" is how long the server took to start "
                       "answering after the request arrived (network time removed). Compare the rows: if the plain file "
                       "is slow too, it's the server/hosting; if only WordPress pages are slow, it's WordPress, plugins "
                       "or the database.</p><div class=tablewrap><table><tr><th>What</th><th class=num>Round trip</th>"
                       "<th class=num>Server work</th><th class=num>Range</th><th class=num>Visits</th><th>Cache</th></tr>"
                       + "".join(timing) + "</table></div>")
        if s.get("static_url"):
            timing_html += f"<p class='muted small'>Plain file used: <code>{e(short_url(s['static_url'], 120))}</code></p>"
    chain = (r.redirects or {}).get("chain") or []
    chain_html = ""
    if len(chain) > 1:
        chain_html = ("<h3>Redirects</h3><div class=tablewrap><table>" + "".join(
            f"<tr><td>{e(status)}</td><td>{e(url)}</td><td class=num>{secs(ms)}</td></tr>" for url, status, ms in chain)
            + "</table></div>")
    return (f'<section class="card"><h2>Server &amp; hosting details</h2><div class=tablewrap><table class="kv">'
            f'{"".join(rows)}</table></div>{timing_html}{chain_html}</section>')


def dns_section(r):
    d = r.dns or {}
    if not d or d.get("skipped"):
        return ""
    rows = []
    for ns in d.get("ns_results") or []:
        if "error" in ns:
            rows.append(f"<tr><td>{e(ns['name'])}</td><td colspan=3 style='color:{COLORS[WARNING]}'>{e(ns['error'])}</td></tr>")
        else:
            rows.append(f"<tr><td>{e(ns['name'])}</td><td class=num>{secs(ns['ms'])}</td><td>{e(', '.join(ns['ips']) or '-')}</td>"
                        f"<td class=num>{e(ns.get('ttl') if ns.get('ttl') is not None else '-')}</td></tr>")
    public = []
    for p in d.get("public") or []:
        if "error" in p:
            public.append(f"<tr><td>{e(p['name'])} ({e(p['ip'])})</td><td colspan=2>{e(p['error'])}</td></tr>")
        else:
            public.append(f"<tr><td>{e(p['name'])} ({e(p['ip'])})</td><td class=num>{secs(p['first_ms'])}</td>"
                          f"<td class=num>{secs(p['cached_ms'])}</td></tr>")
    info = []
    if d.get("provider"):
        info.append(f"DNS provider: <b>{e(d['provider'])}</b>")
    if d.get("zone"):
        info.append(f"Zone: {e(d['zone'])}")
    if d.get("ips"):
        info.append(f"Site address: {e(', '.join(d['ips']))}")
    if d.get("ipv6"):
        info.append("IPv6: yes")
    if d.get("mx"):
        info.append(f"Mail servers: {e(', '.join(d['mx']))}")
    html = f'<section class="card"><h2>DNS details</h2><p class="small">{" &middot; ".join(info)}</p>'
    if rows:
        html += ("<h3>Nameservers (asked directly)</h3><div class=tablewrap><table><tr><th>Nameserver</th>"
                 "<th class=num>Answer time</th><th>Answer</th><th class=num>TTL (s)</th></tr>" + "".join(rows)
                 + "</table></div>")
    if public:
        html += ("<h3>Public DNS services</h3><div class=tablewrap><table><tr><th>Service</th><th class=num>First lookup"
                 "</th><th class=num>Cached</th></tr>" + "".join(public) + "</table></div>")
    return html + "</section>"


def plugins_section(r):
    wp = r.wp or {}
    plugins = sorted((wp.get("plugins") or {}).values(), key=lambda p: (-p.get("bytes", 0), p["name"].lower()))
    theme = wp.get("theme") or {}
    if not plugins and not theme:
        return ""
    rows = []
    for p in plugins:
        status = []
        if p.get("closed"):
            status.append(f'<span style="color:{COLORS[CRITICAL]}">removed from WordPress.org</span>')
        if p.get("version") and p.get("latest") and is_older(p["version"], p["latest"]):
            status.append(f'<span style="color:{COLORS[WARNING]}">update to {e(p["latest"])}</span>')
        if p.get("years_since_update"):
            status.append(f'<span style="color:{COLORS[WARNING]}">not updated for {p["years_since_update"]:.0f} years</span>')
        if not p.get("on_wporg") and not p.get("closed"):
            status.append('<span class="muted">premium/custom</span>')
        rows.append(f"<tr><td><b>{e(p['name'])}</b><br><span class='muted small'>{e(p['slug'])}</span></td>"
                    f"<td>{e(p.get('version') or '?')}{' (guess)' if p.get('version_guess') else ''}</td>"
                    f"<td>{e(p.get('latest') or '-')}</td><td>{e((p.get('last_updated') or '-')[:10])}</td>"
                    f"<td class=num>{p.get('files') or 0}</td><td class=num>{kb(p.get('bytes') or 0)}</td>"
                    f"<td>{' '.join(status) or '-'}</td></tr>")
    t = ""
    if theme:
        t = (f"<p>Theme: <b>{e(theme.get('name'))}</b> {e(theme.get('version') or '')}"
             + (f" (child theme of <b>{e(theme['child_of'])}</b> {e(theme.get('parent_version') or '')})" if theme.get("child_of") else "")
             + (f" - latest {e(theme['latest'])}" if theme.get("latest") else "") + "</p>")
    v = f"<p>WordPress <b>{e(wp.get('version') or 'version hidden')}</b>" + (
        f" (latest {e(wp['latest_version'])})" if wp.get("latest_version") else "") + "</p>"
    extra = ""
    if wp.get("unknown_namespaces"):
        extra = (f"<p class='muted small'>Other add-ons seen through the REST API: "
                 f"{e(', '.join(wp['unknown_namespaces']))}</p>")
    return (f'<section class="card"><h2>WordPress, theme &amp; plugins</h2>{v}{t}'
            "<p class='muted small'>Only plugins that leave a trace on the public site can be seen from outside - "
            "admin-only plugins won't appear. \"Files\" and \"Size\" are what each plugin adds to this page.</p>"
            + ("<div class=tablewrap><table><tr><th>Plugin</th><th>Installed</th><th>Latest</th><th>Last updated</th>"
               "<th class=num>Files</th><th class=num>Size</th><th>Status</th></tr>" + "".join(rows) + "</table></div>"
               if rows else "<p>No plugins were visible from outside.</p>") + extra + "</section>")


def files_section(r):
    d = (r.browser or {}).get("desktop") or {}
    reqs = d.get("requests") if d and not d.get("error") else None
    if reqs:
        items = [(q["size"], q["url"], q["ms"], q.get("type", "")) for q in reqs]
    else:
        items = [(a.transfer, a.url, a.resp.timing.total if a.resp else 0, a.kind) for a in r.assets or []]
    items.sort(key=lambda x: -x[0])
    if not items:
        return ""
    rows = []
    for size, url, ms, kind in items[:30]:
        owner, slug = owner_of(url, r.site_host)
        who = {"plugin": f"plugin: {slug}", "theme": f"theme: {slug}", "core": "WordPress", "uploads": "your uploads",
               "other site": slug, "site": "your site"}.get(owner, owner)
        rows.append(f"<tr><td class=num>{kb(size)}</td><td class=num>{secs(ms)}</td><td>{e(kind)}</td><td>{e(who)}</td>"
                    f"<td class=small>{e(short_url(url, 110))}</td></tr>")
    return (f'<section class="card"><h2>Biggest files</h2><p class="muted small">The {min(30, len(items))} largest of '
            f"{len(items)} files the page downloads.</p><div class=tablewrap><table><tr><th class=num>Size</th>"
            "<th class=num>Time</th><th>Type</th><th>From</th><th>Address</th></tr>" + "".join(rows)
            + "</table></div></section>")


def html_report(r):
    findings = r.findings
    d = r.diagnosis or {}
    when = r.started.strftime("%d %B %Y, %H:%M")
    title = f"{r.domain} - {APP_NAME} report"
    g = r.grade or "?"
    out = [f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
           f"content='width=device-width,initial-scale=1'><title>{e(title)}</title><style>{CSS}</style></head><body>"
           f"<div class=wrap><header class=top><h1>{e(r.domain)}</h1><div class=meta>{e(APP_NAME)} report &middot; "
           f"{e(when)} &middot; <a href='{e(r.final_url or r.start_url)}'>{e(short_url(r.final_url or r.start_url, 70))}"
           "</a></div></header>"]
    if r.cancelled:
        out.append(f'<div class=card style="border-color:{COLORS[WARNING]}">The test was stopped before it finished, so '
                   "this report is incomplete.</div>")
    out.append(f'<section class="card hero"><div class=grade style="background:{GRADE_COLORS.get(g, "#5f6368")}">'
               f"<b>{e(g)}</b><span>{e(r.score if r.score is not None else '?')}/100</span></div>"
               f"<div class=text><h2>{e(d.get('headline', ''))}</h2>"
               + "".join(f"<p>{e(p)}</p>" for p in d.get("paragraphs", [])) + "</div></section>")

    areas = where_is_the_problem(findings)
    if areas:
        out.append('<section class="card"><h2>Where\'s the problem?</h2><div class=areas>' + "".join(
            f'<div class=area style="border-left-color:{COLORS[status]}"><b>{ICONS[status]} {e(label)}</b>'
            f"<small>{e(text)}</small></div>" for label, status, text in areas) + "</div></section>")
    out.append(breakdown_section(r))
    fixes = top_fixes(findings, 10)
    if fixes:
        out.append('<section class="card"><h2>Fix these first</h2><ol class=fixes>' + "".join(
            f"<li>{finding_html(f)}</li>" for f in fixes) + "</ol></section>")
    out.append(browser_section(r))

    for key, cat_title, _ in CATEGORIES:
        mine = sorted([f for f in findings if f.category == key], key=lambda f: f.sort_key)
        if not mine:
            continue
        score = category_score(findings, key)
        out.append(f'<section class="card"><h2>{e(cat_title)} <span class=score style="background:{score_color(score)}">'
                   f"{score}/100</span></h2>" + "".join(finding_html(f) for f in mine) + "</section>")
        if key == "plugins":
            out.append(plugins_section(r))
        elif key == "server":
            out.append(server_section(r))
        elif key == "dns":
            out.append(dns_section(r))
    if not any(f.category == "plugins" for f in findings) and r.is_wp:
        out.append(plugins_section(r))
    out.append(files_section(r))
    if r.errors:
        out.append('<section class="card"><h2>Parts of the test that had problems</h2><details><summary>Show '
                   f"technical details ({len(r.errors)})</summary><ul>" + "".join(
                       f"<li><pre style='white-space:pre-wrap;margin:0'>{e(x)}</pre></li>" for x in r.errors)
                   + "</ul></details></section>")
    out.append(f'<p class="muted small">Made by {e(APP_NAME)} {e(VERSION)} in {r.duration:.0f} seconds. Speed tests '
               "always vary a little - run the test again to confirm, and test after each change you make. Server "
               "location data from ipinfo.io; plugin data from WordPress.org; Google data from PageSpeed Insights."
               "</p></div></body></html>")
    return "".join(x for x in out if x)


def summary_html(r):
    """Simple HTML for the app window (Qt's rich text supports only basic HTML)."""
    d = r.diagnosis or {}
    g = r.grade or "?"
    parts = [f"<table cellspacing=0 cellpadding=0><tr><td style='padding-right:16px'>"
             f"<div style='font-size:44px;font-weight:bold;color:{GRADE_COLORS.get(g, '#5f6368')}'>{e(g)}</div>"
             f"<div style='color:#5f6368'>{e(r.score if r.score is not None else '?')}/100</div></td>"
             f"<td><h2 style='margin:0'>{e(d.get('headline', ''))}</h2>"
             + "".join(f"<p style='margin:6px 0'>{e(p)}</p>" for p in d.get("paragraphs", [])) + "</td></tr></table>"]
    if r.cancelled:
        parts.append(f"<p style='color:{COLORS[WARNING]}'><b>The test was stopped early - results are incomplete.</b></p>")
    areas = where_is_the_problem(r.findings)
    if areas:
        parts.append("<h3>Where's the problem?</h3><table cellspacing=0 cellpadding=3>" + "".join(
            f"<tr><td style='color:{COLORS[s]};font-weight:bold'>{ICONS[s]}</td><td><b>{e(label)}</b></td>"
            f"<td style='color:#5f6368'>{e(text)}</td></tr>" for label, s, text in areas) + "</table>")
    fixes = top_fixes(r.findings, 8)
    if fixes:
        parts.append("<h3>Fix these first</h3><ol>" + "".join(
            f"<li style='margin-bottom:8px'><b style='color:{COLORS[f.severity]}'>{e(f.title)}</b>"
            + (f"<br>{e(f.fix)}" if f.fix else "") + "</li>" for f in fixes) + "</ol>")
    parts.append("<p style='color:#5f6368'>Open the full report for every detail, the plugin list, server timings and "
                 "screenshots.</p>")
    return "".join(parts)
