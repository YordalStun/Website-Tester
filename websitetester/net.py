"""Timed web requests.

Unlike a normal download library this measures each stage separately - looking up the address (DNS),
connecting, the SSL handshake, waiting for the server to start answering, and downloading - so we can
tell which one is slow.
"""
import gzip
import http.client
import json
import socket
import ssl
import time
import urllib.request
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

try:
    import brotli
except ImportError:  # pragma: no cover - brotli is in requirements, but carry on without it
    brotli = None

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
ACCEPT_ENCODING = "gzip, deflate, br" if brotli else "gzip, deflate"
MAX_BODY = 20 * 1024 * 1024
REDIRECT_CODES = (301, 302, 303, 307, 308)


def ms_since(start):
    return (time.perf_counter() - start) * 1000.0


@dataclass
class Timing:
    """All in milliseconds."""
    dns: float = 0.0
    connect: float = 0.0  # TCP connect = one round trip to the server
    tls: float = 0.0
    wait: float = 0.0  # request sent -> response headers back (server thinking + one round trip)
    download: float = 0.0

    @property
    def total(self):
        return self.dns + self.connect + self.tls + self.wait + self.download

    @property
    def think(self):
        """Roughly how long the server spent working on the request (wait minus the network round trip)."""
        return max(0.0, self.wait - self.connect)


@dataclass
class Response:
    url: str
    status: int = 0
    reason: str = ""
    headers: dict = field(default_factory=dict)  # lower-case name -> value
    raw_headers: list = field(default_factory=list)
    body: bytes = b""
    transfer_size: int = 0  # body bytes as sent over the network (compressed)
    timing: Timing = field(default_factory=Timing)
    ip: str = ""
    tls_version: str = ""
    cipher: str = ""
    alpn: str = ""
    cert: dict = field(default_factory=dict)
    cert_error: str = ""
    error: str = ""
    truncated: bool = False

    @property
    def ok(self):
        return not self.error and 200 <= self.status < 400

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    @property
    def content_type(self):
        return self.header("content-type").split(";")[0].strip().lower()

    @property
    def is_redirect(self):
        return self.status in REDIRECT_CODES and bool(self.header("location"))

    @property
    def text(self):
        charset = "utf-8"
        for part in self.header("content-type").split(";")[1:]:
            name, _, value = part.strip().partition("=")
            if name.lower() == "charset" and value:
                charset = value.strip("\"' ")
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def decompress(raw, encoding):
    encoding = (encoding or "").lower().strip()
    if not raw or encoding in ("", "identity"):
        return raw
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompress(raw, 16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    if encoding == "br" and brotli:
        return brotli.decompress(raw)
    raise ValueError(f"unknown content-encoding {encoding}")


def _tls_context(verify, alpn):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(list(alpn))
    return ctx


def _connect(infos, timeout):
    """Try each address in turn. Returns (socket, ip, ms for the successful connect)."""
    last_error = None
    for family, kind, proto, _, addr in infos:
        sock = socket.socket(family, kind, proto)
        sock.settimeout(timeout)
        start = time.perf_counter()
        try:
            sock.connect(addr)
        except OSError as e:
            last_error = e
            sock.close()
            continue
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock, addr[0], ms_since(start)
    raise last_error or OSError("no addresses")


def _describe_error(e):
    if isinstance(e, socket.timeout):
        return "timed out"
    text = getattr(e, "strerror", None) or str(e) or e.__class__.__name__
    return text


def fetch(url, method="GET", headers=None, data=None, timeout=20.0, read_body=True, alpn=("http/1.1",),
          max_body=MAX_BODY):
    """Make one request on a brand-new connection (like a first-time visitor) and time every stage."""
    resp = Response(url)
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        resp.error = "not a web address"
        return resp
    https = parts.scheme == "https"
    try:
        port = parts.port or (443 if https else 80)
    except ValueError:
        resp.error = "bad port number"
        return resp
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    t = resp.timing

    start = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        resp.error = f"couldn't look up the address ({_describe_error(e)})"
        return resp
    t.dns = ms_since(start)

    try:
        sock, resp.ip, t.connect = _connect(infos, timeout)
    except OSError as e:
        resp.error = f"couldn't connect to the server ({_describe_error(e)})"
        return resp

    if https:
        start = time.perf_counter()
        try:
            sock = _tls_context(True, alpn).wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as e:
            resp.cert_error = e.verify_message or str(e)
            # Carry on without checking the certificate so the other tests can still run.
            sock.close()
            try:
                sock, _, t.connect = _connect([i for i in infos if i[4][0] == resp.ip] or infos, timeout)
                start = time.perf_counter()
                sock = _tls_context(False, alpn).wrap_socket(sock, server_hostname=host)
            except (ssl.SSLError, OSError) as e2:
                resp.error = f"secure (SSL) connection failed ({_describe_error(e2)})"
                return resp
        except (ssl.SSLError, OSError) as e:
            sock.close()
            resp.error = f"secure (SSL) connection failed ({_describe_error(e)})"
            return resp
        t.tls = ms_since(start)
        resp.tls_version = sock.version() or ""
        resp.cipher = (sock.cipher() or ("",))[0]
        resp.alpn = sock.selected_alpn_protocol() or ""
        try:
            resp.cert = sock.getpeercert() or {}
        except ValueError:
            resp.cert = {}

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock
    request_headers = {
        "Host": parts.netloc.rsplit("@", 1)[-1],
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": ACCEPT_ENCODING,
        "Accept-Language": "en-GB,en;q=0.9",
        "Connection": "close",
    }
    request_headers.update(headers or {})
    start = time.perf_counter()
    try:
        conn.request(method, path, body=data, headers=request_headers)
        r = conn.getresponse()
    except (OSError, http.client.HTTPException) as e:
        conn.close()
        resp.error = f"the server didn't answer ({_describe_error(e)})"
        return resp
    t.wait = ms_since(start)
    resp.status, resp.reason = r.status, r.reason
    resp.raw_headers = r.getheaders()
    for name, value in resp.raw_headers:
        key = name.lower()
        resp.headers[key] = f"{resp.headers[key]}, {value}" if key in resp.headers else value

    if read_body and method != "HEAD":
        start = time.perf_counter()
        try:
            raw = r.read(max_body + 1)
        except (OSError, http.client.HTTPException) as e:
            raw = b""
            resp.error = f"download was cut off ({_describe_error(e)})"
        t.download = ms_since(start)
        if len(raw) > max_body:
            raw = raw[:max_body]
            resp.truncated = True
        resp.transfer_size = len(raw)
        try:
            resp.body = decompress(raw, resp.header("content-encoding")) if not resp.truncated else raw
        except Exception:  # bad compressed data (brotli raises its own error type) - keep what we got
            resp.body = raw
    conn.close()
    return resp


def follow(url, max_redirects=10, **kwargs):
    """Fetch a page, following redirects. Returns every response in the chain (the last is the real page)."""
    chain = []
    seen = set()
    while True:
        resp = fetch(url, **kwargs)
        chain.append(resp)
        if not resp.is_redirect or len(chain) > max_redirects:
            return chain
        url = urljoin(url, resp.header("location"))
        if url in seen:
            resp.error = "redirect loop"
            return chain
        seen.add(url)


def probe_alpn(host, port=443, timeout=10.0):
    """Which protocol the server picks when we offer HTTP/2: 'h2', 'http/1.1' or '' (didn't say)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        sock, _, _ = _connect(infos, timeout)
        with _tls_context(False, ("h2", "http/1.1")).wrap_socket(sock, server_hostname=host) as s:
            return s.selected_alpn_protocol() or ""
    except (OSError, ssl.SSLError, UnicodeError):
        return None


def cert_summary(cert):
    """Turn Python's certificate dict into something readable."""
    if not cert:
        return {}
    def name_of(seq):
        out = {}
        for rdn in seq or ():
            for key, value in rdn:
                out[key] = value
        return out
    issuer = name_of(cert.get("issuer"))
    subject = name_of(cert.get("subject"))
    expires = cert.get("notAfter")
    expires_ts = ssl.cert_time_to_seconds(expires) if expires else None
    return {
        "issuer": issuer.get("organizationName") or issuer.get("commonName", ""),
        "subject": subject.get("commonName", ""),
        "names": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"],
        "expires": expires,
        "days_left": int((expires_ts - time.time()) // 86400) if expires_ts else None,
    }


_opener = None


def get_json(url, timeout=15.0, data=None, headers=None):
    """Simple JSON GET for outside services (WordPress.org, Google, IP location). Raises on failure."""
    global _opener
    if _opener is None:
        _opener = urllib.request.build_opener()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "WebsiteTester/1.0", **(headers or {})})
    with _opener.open(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def host_of(url):
    return (urlsplit(url).hostname or "").lower()


def same_site(host_a, host_b):
    """True for example.com vs www.example.com / cdn.example.com (roughly - good enough for reports)."""
    def base(h):
        h = h.lower().strip(".")
        parts = h.split(".")
        if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac", "gov", "ltd", "plc", "me"):
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])
    return base(host_a) == base(host_b)
