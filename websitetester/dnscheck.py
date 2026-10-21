"""DNS: how quickly the domain name turns into a server address, and whether the setup is healthy."""
import ipaddress
import socket
import statistics
import time

import dns.exception
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rdatatype
import dns.resolver

from .findings import CRITICAL, GOOD, INFO, WARNING, Finding

PUBLIC_RESOLVERS = [("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9")]

# Part of a nameserver's name -> who runs it
DNS_PROVIDERS = [
    ("cloudflare.com", "Cloudflare"), ("domaincontrol.com", "GoDaddy"), ("awsdns", "Amazon Route 53"),
    ("azure-dns", "Microsoft Azure DNS"), ("googledomains.com", "Google Cloud DNS"), ("nsone.net", "NS1"),
    ("dnsimple", "DNSimple"), ("dnsmadeeasy", "DNS Made Easy"), ("ultradns", "UltraDNS"),
    ("registrar-servers.com", "Namecheap"), ("namecheaphosting", "Namecheap hosting"), ("123-reg", "123 Reg"),
    ("ui-dns", "IONOS"), ("ionos", "IONOS"), ("dns-parking.com", "Hostinger"), ("hostinger", "Hostinger"),
    ("bluehost", "Bluehost"), ("hostgator", "HostGator"), ("siteground", "SiteGround"), ("wpengine", "WP Engine"),
    ("kinsta", "Kinsta"), ("digitalocean", "DigitalOcean"), ("linode", "Linode / Akamai"), ("vultr", "Vultr"),
    ("hetzner", "Hetzner"), ("ovh", "OVH"), ("hover.com", "Hover"), ("name.com", "Name.com"), ("gandi", "Gandi"),
    ("cloudns", "ClouDNS"), ("dynect", "Oracle Dyn"), ("akam", "Akamai"), ("heartinternet", "Heart Internet"),
    ("names.co.uk", "Namesco"), ("krystal", "Krystal"), ("tsohost", "TSOHost"), ("fasthosts", "Fasthosts"),
    ("easydns", "easyDNS"), ("he.net", "Hurricane Electric"), ("wixdns", "Wix"), ("squarespacedns", "Squarespace"),
    ("shopify", "Shopify"), ("porkbun", "Porkbun"), ("dreamhost", "DreamHost"), ("a2hosting", "A2 Hosting"),
    ("inmotionhosting", "InMotion"), ("websitewelcome", "HostGator"), ("hostpapa", "HostPapa"),
    ("20i.com", "20i"), ("stackdns", "20i"), ("cloudways", "Cloudways"), ("pantheon", "Pantheon"),
    ("one.com", "one.com"), ("zoneedit", "ZoneEdit"), ("bunny.net", "Bunny DNS"), ("vercel-dns", "Vercel"),
    ("netlify", "Netlify"), ("hostingukdns", "Hosting UK"), ("cpanel", "cPanel server"),
]


def is_ip(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def provider_of(ns_names):
    for ns in ns_names:
        low = ns.lower()
        for key, name in DNS_PROVIDERS:
            if key in low:
                return name
    return ""


def _resolver(nameserver=None, timeout=4.0):
    r = dns.resolver.Resolver(configure=nameserver is None)
    if nameserver:
        r.nameservers = [nameserver]
    r.lifetime = timeout
    r.timeout = timeout
    return r


def _timed_query(name, rdtype, server_ip, timeout, recursion):
    q = dns.message.make_query(name, rdtype)
    if not recursion:
        q.flags &= ~dns.flags.RD
    start = time.perf_counter()
    try:
        r = dns.query.udp(q, server_ip, timeout=timeout)
    except dns.message.Truncated:
        r = dns.query.tcp(q, server_ip, timeout=timeout)
    return r, (time.perf_counter() - start) * 1000.0


def _a_records(response):
    ips, cnames, ttl = set(), [], None
    for rrset in response.answer:
        if rrset.rdtype == dns.rdatatype.A:
            ips.update(r.address for r in rrset)
            ttl = rrset.ttl if ttl is None else min(ttl, rrset.ttl)
        elif rrset.rdtype == dns.rdatatype.CNAME:
            cnames.extend(r.target.to_text().rstrip(".") for r in rrset)
    return ips, cnames, ttl


def _txt(resolver, name):
    try:
        return [b"".join(r.strings).decode("utf-8", "replace") for r in resolver.resolve(name, "TXT")]
    except (dns.exception.DNSException, OSError):
        return []


def check(host, timeout=4.0, check_email=True):
    """Returns (data, findings)."""
    data = {"host": host}
    findings = []
    if is_ip(host) or host == "localhost":
        data["skipped"] = "the address is an IP number, so there's no DNS to test"
        return data, findings

    # 1. What this PC sees (may already be cached by Windows)
    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        data["system_ms"] = (time.perf_counter() - start) * 1000.0
        data["ips"] = sorted({i[4][0] for i in infos})
    except (socket.gaierror, UnicodeError) as e:
        data["system_error"] = str(e)
        findings.append(Finding(
            "dns", CRITICAL, "The domain name doesn't lead anywhere",
            f"{host} couldn't be turned into a server address ({e}). Visitors can't reach the site at all.",
            "Check the domain hasn't expired (with your registrar) and that its A record points at your "
            "server's IP address in your DNS settings.", impact=100000))
        return data, findings

    resolver = _resolver(timeout=timeout)

    # 2. Find the zone and its nameservers
    try:
        zone = dns.resolver.zone_for_name(host, resolver=resolver, lifetime=timeout).to_text().rstrip(".")
        data["zone"] = zone
        ns_names = sorted(r.target.to_text().rstrip(".") for r in resolver.resolve(zone, "NS"))
    except (dns.exception.DNSException, OSError) as e:
        data["ns_error"] = str(e)
        findings.append(Finding("dns", INFO, "Couldn't look up the nameservers",
                                f"The detailed DNS tests were skipped: {e}. This often means this PC's network "
                                "blocks direct DNS queries - it isn't necessarily a problem with the site."))
        return data, findings
    data["nameservers"] = ns_names
    data["provider"] = provider_of(ns_names)

    # 3. Ask each nameserver directly (what a visitor's internet provider has to do when it isn't cached)
    ns_results = []
    for ns in ns_names:
        entry = {"name": ns}
        try:
            ns_ip = next(iter(resolver.resolve(ns, "A"))).address
            entry["ip"] = ns_ip
            times = []
            for _ in range(3):
                response, elapsed = _timed_query(host, "A", ns_ip, timeout, recursion=False)
                times.append(elapsed)
            ips, cnames, ttl = _a_records(response)
            entry.update(ms=min(times), ms_all=times, ips=sorted(ips), cnames=cnames, ttl=ttl,
                         authoritative=bool(response.flags & dns.flags.AA))
        except (dns.exception.DNSException, OSError, StopIteration) as e:
            entry["error"] = str(e) or e.__class__.__name__
        ns_results.append(entry)
    data["ns_results"] = ns_results

    working = [e for e in ns_results if "ms" in e]
    failed = [e for e in ns_results if "error" in e]
    provider = data["provider"] or "your DNS provider"
    if not working:
        findings.append(Finding(
            "dns", INFO, "None of the nameservers answered our direct questions",
            "We couldn't time the nameservers directly. Either they are down, or this PC's network blocks direct "
            "DNS queries (common on office networks). The site itself still resolved, so visitors can reach it.",
            items=[f"{e['name']}: {e['error']}" for e in failed]))
    else:
        avg = statistics.mean(e["ms"] for e in working)
        data["auth_ms"] = avg
        slowest = max(working, key=lambda e: e["ms"])
        if avg > 400:
            findings.append(Finding(
                "dns", CRITICAL, f"Very slow DNS ({avg:.0f} ms)",
                f"The nameservers ({provider}) take {avg:.0f} ms on average to answer. Every new visitor waits for "
                "this before anything else can happen.",
                "Move DNS to a fast, worldwide (\"anycast\") provider. Cloudflare DNS is free and typically answers "
                "in under 30 ms. Your domain stays with the same registrar - you only change the nameservers.",
                impact=int(avg)))
        elif avg > 150:
            findings.append(Finding(
                "dns", WARNING, f"Slow DNS ({avg:.0f} ms)",
                f"The nameservers ({provider}) take {avg:.0f} ms on average to answer (fast providers answer in "
                f"under 50 ms). The slowest is {slowest['name']} at {slowest['ms']:.0f} ms.",
                "Consider moving DNS to a faster anycast provider such as Cloudflare (free).", impact=int(avg - 50)))
        else:
            findings.append(Finding("dns", GOOD, f"DNS answers quickly ({avg:.0f} ms)",
                                    f"The nameservers ({provider}) answer fast."))
        if failed:
            findings.append(Finding(
                "dns", CRITICAL if len(failed) >= len(working) else WARNING,
                f"{len(failed)} of {len(ns_results)} nameservers didn't answer",
                "When a nameserver doesn't answer, visitors' devices wait for it to time out (often 1-3 seconds) "
                "before trying another one.",
                "Remove dead nameservers from the domain at your registrar, or ask your DNS provider to fix them.",
                impact=1500, items=[f"{e['name']}: {e['error']}" for e in failed]))
        answer_sets = {tuple(e["ips"]) for e in working if e["ips"]}
        if len(answer_sets) > 1:
            findings.append(Finding(
                "dns", WARNING, "The nameservers give different answers",
                "Different nameservers point the site at different servers, so some visitors may get an old or "
                "wrong server.", "Make the A records the same at every nameserver (or remove the old ones).",
                items=[f"{e['name']}: {', '.join(e['ips']) or '(no A record)'}" for e in working]))
        ttls = [e["ttl"] for e in working if e.get("ttl") is not None]
        if ttls:
            ttl = min(ttls)
            data["ttl"] = ttl
            if ttl < 300:
                findings.append(Finding(
                    "dns", WARNING, f"Very short DNS cache time (TTL {ttl} seconds)",
                    "Internet providers are told to forget the address after only "
                    f"{ttl} seconds, so visitors have to do the slow full lookup much more often.",
                    "Set the TTL of the site's A/CNAME records to 3600 (1 hour) unless you're about to move servers.",
                    impact=60))
        cnames = next((e["cnames"] for e in working if e.get("cnames")), [])
        data["cnames"] = cnames
    if len(ns_names) < 2:
        findings.append(Finding("dns", WARNING, "Only one nameserver",
                                "If it goes down, the whole site (and email) disappears.",
                                "Use at least two nameservers, ideally on different networks."))

    # 4. Full lookups through big public DNS services (like a visitor's provider would do)
    public = []
    for label, ip in PUBLIC_RESOLVERS:
        entry = {"name": label, "ip": ip}
        try:
            _, first = _timed_query(host, "A", ip, timeout, recursion=True)
            r2, second = _timed_query(host, "A", ip, timeout, recursion=True)
            entry.update(first_ms=first, cached_ms=second, ips=sorted(_a_records(r2)[0]))
        except (dns.exception.DNSException, OSError) as e:
            entry["error"] = str(e) or e.__class__.__name__
        public.append(entry)
    data["public"] = public

    # 5. Extra: IPv6 and the CNAME chain
    try:
        data["ipv6"] = sorted(r.address for r in resolver.resolve(host, "AAAA"))
    except (dns.exception.DNSException, OSError):
        data["ipv6"] = []
    chain = data.get("cnames") or []
    if len(chain) >= 2:
        findings.append(Finding(
            "dns", WARNING, f"Long DNS chain ({len(chain)} steps)",
            "The name points at another name, which points at another... each step can add a lookup.",
            "Point the record more directly at the final server if your host allows it.", impact=40 * len(chain),
            items=[host] + chain))

    if check_email:
        _check_email(data["zone"], resolver, data, findings)
    return data, findings


def _check_email(zone, resolver, data, findings):
    try:
        mx = sorted((r.preference, r.exchange.to_text().rstrip(".")) for r in resolver.resolve(zone, "MX"))
    except (dns.exception.DNSException, OSError):
        mx = []
    data["mx"] = [f"{p} {h}" for p, h in mx]
    spf = [t for t in _txt(resolver, zone) if t.lower().startswith("v=spf1")]
    dmarc = [t for t in _txt(resolver, "_dmarc." + zone) if t.lower().startswith("v=dmarc1")]
    data["spf"], data["dmarc"] = spf, dmarc
    if not mx:
        findings.append(Finding("email", INFO, "No email (MX) records",
                                f"{zone} isn't set up to receive email. That's fine if you don't use email on this "
                                "domain."))
        if not spf:
            findings.append(Finding(
                "email", WARNING, "No SPF record",
                "Without SPF, scammers can more easily send email pretending to be from this domain.",
                f"Add a TXT record on {zone}: v=spf1 -all (if the domain never sends email)."))
        return
    findings.append(Finding("email", GOOD, "Email (MX) records are set up", items=data["mx"]))
    if not spf:
        findings.append(Finding(
            "email", WARNING, "No SPF record",
            "SPF tells other mail servers which servers may send email for this domain. Without it, your emails "
            "(including WordPress contact form and order emails) are more likely to land in spam.",
            "Add a TXT record starting with v=spf1 - your email provider's help pages give the exact text."))
    elif len(spf) > 1:
        findings.append(Finding("email", WARNING, "More than one SPF record",
                                "Having two SPF records breaks SPF completely, so emails may go to spam.",
                                "Merge them into a single TXT record.", items=spf))
    else:
        findings.append(Finding("email", GOOD, "SPF record found", items=spf))
    if not dmarc:
        findings.append(Finding(
            "email", WARNING, "No DMARC record",
            "Gmail and Yahoo now expect a DMARC record for reliable delivery.",
            f"Add a TXT record on _dmarc.{zone} with: v=DMARC1; p=none; rua=mailto:you@{zone} "
            "(then tighten to p=quarantine once reports look clean)."))
    else:
        findings.append(Finding("email", GOOD, "DMARC record found", items=dmarc))
