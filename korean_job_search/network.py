"""Credential-free, SSRF-resistant public HTTP fetching using only the stdlib.

DNS answers are validated *in their entirety*, then numeric addresses are pinned
at the socket layer.  No urllib opener, proxy environment, cookie jar, netrc or
ambient authentication is used.  TLS still verifies the original DNS hostname.
A single monotonic deadline covers DNS, robots, pacing, redirects and every I/O
operation (including slow-drip HTTP headers).  Both encoded and decoded bodies
are bounded; HTTP headers/framing and the number of requests are bounded too.

``respect_robots=False`` is an explicit policy override, not an SSRF override.
The default fails closed on unavailable or uninterpretable robots policies.
Robots redirects within the policy's own origin bootstrap that policy; redirects
to another origin require that origin's policy first.  Policy cycles deny access.

The private _validate_url, _resolve_addresses, _connect and _wire_request seams
are deliberately independent so safety tests can run without a live network.
"""

from __future__ import annotations

import http.client
import io
import ipaddress
import math
import queue
import re
import socket
import ssl
import threading
import time
import unicodedata
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from email.message import Message
from urllib.parse import parse_qsl, quote, urljoin, urlsplit, urlunsplit


_USER_AGENT = "KoreanJobSearch/0.1 (public-only; respects robots.txt)"
_ROBOT_TOKEN = "KoreanJobSearch"
_MAX_REDIRECTS = 5
_MAX_REQUESTS = 20
_MAX_URL_BYTES = 8192
_MAX_HEADER_BYTES = 65536
_ROBOTS_MAX_BYTES = 512000
_ROBOTS_CACHE_SECONDS = 900
_ROBOTS_FAILURE_CACHE_SECONDS = 60
_MAX_CACHED_ORIGINS = 128
_MAX_ROBOTS_RULES = 4096
_MIN_INTERVAL = 0.2
_READ_SIZE = 65536
# getaddrinfo cannot be cancelled portably. Daemon workers do DNS only (never
# connect), and this process-wide bound prevents unbounded abandoned threads.
_DNS_SLOTS = threading.BoundedSemaphore(8)
_REDIRECTS = {301, 302, 303, 307, 308}
_LOCAL_SUFFIXES = ("localhost", "local", "internal", "lan", "home", "home.arpa", "invalid", "test", "onion")
_CREDENTIAL_KEYS = {
    "access_token", "refresh_token", "id_token", "token", "api_key", "apikey",
    "password", "passwd", "authorization", "auth", "credential", "credentials",
    "signature", "x_amz_signature", "x_amz_credential", "x_goog_signature", "x_goog_credential",
}
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# Explicit additions keep conservative classification consistent on Python 3.10+
# rather than inheriting the host Python's version of the IANA special-use list.
_V4_SPECIAL = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16",
    "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
_V6_GLOBAL = ipaddress.ip_network("2000::/3")
_V6_SPECIAL = tuple(ipaddress.ip_network(value) for value in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20",
))


class _Failure(Exception):
    """Only static, credential-free messages may cross the public boundary."""

    def __init__(self, status: str, message: str, http_status: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class _Target:
    url: str
    scheme: str
    host: str
    port: int
    host_header: str
    request_target: str

    @property
    def origin(self) -> tuple[str, str, int]:
        return self.scheme, self.host, self.port

    @property
    def origin_url(self) -> str:
        return self.scheme + "://" + self.host_header


@dataclass(frozen=True)
class _Address:
    family: int
    sockaddr: tuple


@dataclass(frozen=True)
class _Response:
    status: int
    headers: Message
    content: bytes


class _Deadline:
    def __init__(self, timeout: float):
        self.expires = time.monotonic() + timeout
        self.redirects = 0
        self.requests = 0

    def remaining(self) -> float:
        remaining = self.expires - time.monotonic()
        if remaining <= 0:
            raise _Failure("error", "Total request timeout exceeded.")
        return remaining

    def redirect(self) -> None:
        self.remaining()
        self.redirects += 1
        if self.redirects > _MAX_REDIRECTS:
            raise _Failure("error", "Redirect limit exceeded (including robots redirects).")

    def request(self) -> None:
        self.remaining()
        self.requests += 1
        if self.requests > _MAX_REQUESTS:
            raise _Failure("error", "Request limit exceeded (including robots requests).")


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global or any((address.is_private, address.is_loopback, address.is_link_local,
                                     address.is_reserved, address.is_multicast, address.is_unspecified)):
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return not any(address in block for block in _V4_SPECIAL)
    # Reject mapped, NAT64, Teredo and 6to4 paths rather than delegating their
    # embedded destination semantics to the local network configuration.
    return address in _V6_GLOBAL and not any(address in block for block in _V6_SPECIAL)


def _unsafe_characters(value: str) -> bool:
    return any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf", "Cs"} or char == "\\" for char in value)


def _validate_url(url: str) -> _Target:
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_BYTES or _unsafe_characters(url):
        raise _Failure("blocked", "Invalid URL: empty, oversized, whitespace, control or backslash characters.")
    if re.search(r"%(?![0-9a-fA-F]{2})|%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)", url, re.I):
        raise _Failure("blocked", "Invalid URL: unsafe or malformed percent encoding.")
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            raise _Failure("unsupported", "Only public HTTP and HTTPS URLs are supported.")
        if not parts.netloc or "@" in parts.netloc:
            raise _Failure("blocked", "A public hostname is required; URL userinfo is forbidden.")
        scheme = parts.scheme.lower()
        port = 443 if scheme == "https" else 80
        raw_host = parts.hostname
        if not raw_host or "%" in raw_host:
            raise _Failure("blocked", "Invalid hostname or IPv6 zone identifier.")
        # Check the port lexically too; urlsplit otherwise normalizes :080 or :.
        if parts.netloc.startswith("["):
            suffix = parts.netloc[parts.netloc.index("]") + 1:]
        else:
            if parts.netloc.count(":") > 1:
                raise _Failure("blocked", "IPv6 literals must use brackets.")
            suffix = ":" + parts.netloc.split(":", 1)[1] if ":" in parts.netloc else ""
        if suffix and suffix != ":" + str(port):
            raise _Failure("blocked", "Only the scheme's default port is permitted.")
        if parts.port is not None and parts.port != port:
            raise _Failure("blocked", "Custom ports are forbidden.")
        host = raw_host.encode("idna").decode("ascii").lower()
        if host.endswith("."):
            host = host[:-1]
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if not _is_public_ip(literal):
                raise _Failure("blocked", "Non-public, local or reserved IP addresses are forbidden.")
            host = str(literal)
        else:
            labels = host.split(".")
            if len(host) > 253 or len(labels) < 2 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            ) or labels[-1].isdigit():
                raise _Failure("blocked", "Invalid public DNS hostname or nonstandard IP notation.")
            if any(host == suffix or host.endswith("." + suffix) for suffix in _LOCAL_SUFFIXES):
                raise _Failure("blocked", "Local and special-use DNS names are forbidden.")
        for key, _ in parse_qsl(parts.query, keep_blank_values=True, max_num_fields=256):
            if key.lower().replace("-", "_") in _CREDENTIAL_KEYS:
                raise _Failure("needs_credentials", "Credential-bearing URLs are not fetched; provide a public URL.")
        host_header = "[" + host + "]" if ":" in host else host
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="%/?:@!$&'()*+,;=-._~")
        request_target = path + ("?" + query if query else "")
        normalized = urlunsplit((scheme, host_header, path, query, ""))
        if len(normalized.encode("ascii")) > _MAX_URL_BYTES:
            raise _Failure("blocked", "Encoded URL exceeds the size limit.")
        return _Target(normalized, scheme, host, port, host_header, request_target)
    except (ValueError, UnicodeError, IndexError):
        raise _Failure("blocked", "Malformed URL, hostname, port or query.") from None


def _timed_getaddrinfo(host: str, port: int, deadline: _Deadline) -> list:
    deadline.remaining()
    if not _DNS_SLOTS.acquire(blocking=False):
        raise _Failure("error", "DNS resolver capacity exhausted; try again later.")
    answers: queue.Queue = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            records = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC,
                                         type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
            answers.put((True, records))
        except Exception:
            # Resolver messages can contain user input or local configuration.
            answers.put((False, None))
        finally:
            _DNS_SLOTS.release()

    try:
        worker = threading.Thread(target=resolve, name="public-fetch-dns", daemon=True)
        worker.start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise _Failure("error", "DNS resolver worker could not start.") from None
    try:
        success, records = answers.get(timeout=deadline.remaining())
    except queue.Empty:
        raise _Failure("error", "Total request timeout exceeded during DNS resolution.") from None
    deadline.remaining()
    if not success:
        raise _Failure("error", "Public hostname DNS resolution failed.")
    return records


def _resolve_addresses(target: _Target, deadline: _Deadline) -> tuple[_Address, ...]:
    deadline.remaining()
    try:
        literal = ipaddress.ip_address(target.host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(literal):
            raise _Failure("blocked", "Non-public IP address rejected.")
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        sockaddr = (str(literal), target.port, 0, 0) if literal.version == 6 else (str(literal), target.port)
        return (_Address(family, sockaddr),)
    records = _timed_getaddrinfo(target.host, target.port, deadline)
    if not records or len(records) > 64:
        raise _Failure("error", "DNS returned an empty or excessive address set.")
    addresses = []
    try:
        for family, kind, protocol, _, sockaddr in records:
            deadline.remaining()
            if family not in {socket.AF_INET, socket.AF_INET6} or kind != socket.SOCK_STREAM or protocol not in {0, socket.IPPROTO_TCP}:
                raise ValueError
            if sockaddr[1] != target.port or "%" in sockaddr[0]:
                raise ValueError
            address = ipaddress.ip_address(sockaddr[0])
            if (address.version == 4) != (family == socket.AF_INET):
                raise ValueError
            if family == socket.AF_INET6 and (len(sockaddr) != 4 or sockaddr[2] != 0 or sockaddr[3] != 0):
                raise ValueError
            if not _is_public_ip(address):
                raise _Failure("blocked", "DNS resolved to a non-public address; the entire answer was rejected.")
            pinned = (str(address), target.port) if address.version == 4 else (str(address), target.port, 0, 0)
            item = _Address(family, pinned)
            if item not in addresses:
                addresses.append(item)
    except (ValueError, TypeError, IndexError):
        raise _Failure("blocked", "DNS returned a malformed or unsafe address record.") from None
    return tuple(addresses)


def _close(sock) -> None:
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def _verify_peer(sock, address: _Address) -> None:
    try:
        peer = sock.getpeername()
        actual = ipaddress.ip_address(peer[0])
        if actual != ipaddress.ip_address(address.sockaddr[0]) or peer[1] != address.sockaddr[1] or not _is_public_ip(actual):
            raise ValueError
    except (OSError, ValueError, TypeError, IndexError):
        raise _Failure("blocked", "Connected peer does not match the validated public address.") from None


def _connect(target: _Target, addresses: tuple[_Address, ...], deadline: _Deadline):
    for address in addresses:
        sock = None
        try:
            deadline.remaining()
            sock = socket.socket(address.family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            sock.settimeout(deadline.remaining())
            # Numeric, validated address: no hidden hostname resolution/rebinding.
            sock.connect(address.sockaddr)
            _verify_peer(sock, address)
            if target.scheme == "https":
                context = ssl.create_default_context()
                sock.settimeout(deadline.remaining())
                sock = context.wrap_socket(sock, server_hostname=target.host, do_handshake_on_connect=False)
                sock.settimeout(deadline.remaining())
                sock.do_handshake()
                _verify_peer(sock, address)
            deadline.remaining()
            return sock
        except _Failure:
            _close(sock)
            raise
        except ssl.SSLCertVerificationError:
            _close(sock)
            raise _Failure("error", "TLS certificate verification failed; no insecure retry was attempted.") from None
        except ssl.SSLError:
            _close(sock)
            raise _Failure("error", "Verified TLS connection failed; no insecure retry was attempted.") from None
        except TimeoutError:
            _close(sock)
            raise _Failure("error", "Network connection timeout exceeded.") from None
        except OSError:
            _close(sock)
    deadline.remaining()
    raise _Failure("error", "Unable to connect to a validated public address.")


class _DeadlineReader(io.RawIOBase):
    """Reapply the absolute deadline before *each* raw socket read."""

    def __init__(self, sock, deadline: _Deadline):
        super().__init__()
        self.sock = sock
        self.deadline = deadline
        self.received = 0
        self.limit = _MAX_HEADER_BYTES

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        remaining_bytes = self.limit - self.received
        if remaining_bytes <= 0:
            raise _Failure("error", "HTTP headers or wire response exceeded the byte limit.")
        self.sock.settimeout(self.deadline.remaining())
        count = self.sock.recv_into(memoryview(buffer)[:remaining_bytes])
        self.deadline.remaining()
        self.received += count
        return count


class _ResponseSocket:
    """HTTPResponse needs makefile(), not a hostname-resolving connection."""

    def __init__(self, reader: _DeadlineReader):
        self.reader = reader

    def makefile(self, mode):
        if mode != "rb":
            raise ValueError("Read-only response socket")
        return io.BufferedReader(self.reader)


def _read_body(response: http.client.HTTPResponse, deadline: _Deadline, max_bytes: int) -> bytes:
    headers = response.headers
    lengths = headers.get_all("Content-Length", [])
    encodings = headers.get_all("Content-Encoding", [])
    transfers = headers.get_all("Transfer-Encoding", [])
    if len(lengths) > 1 or len(encodings) > 1 or len(transfers) > 1 or (lengths and transfers):
        raise _Failure("error", "Ambiguous HTTP response framing.", response.status)
    expected = None
    if lengths:
        length = lengths[0].strip()
        if not re.fullmatch(r"[0-9]{1,18}", length):
            raise _Failure("error", "Invalid HTTP Content-Length.", response.status)
        expected = int(length)
        if expected > max_bytes:
            raise _Failure("error", "Response exceeds the download byte limit.", response.status)
    if transfers and transfers[0].strip().lower() != "chunked":
        raise _Failure("unsupported", "Unsupported HTTP transfer encoding.", response.status)
    encoding = encodings[0].strip().lower() if encodings else "identity"
    if encoding not in {"", "identity", "gzip", "deflate"}:
        raise _Failure("unsupported", "Unsupported response compression; identity encoding was requested.", response.status)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding in {"gzip", "deflate"} else None
    body = bytearray()
    downloaded = 0
    while True:
        deadline.remaining()
        chunk = response.read1(min(_READ_SIZE, max_bytes - downloaded + 1))
        deadline.remaining()
        if not chunk:
            break
        downloaded += len(chunk)
        if downloaded > max_bytes:
            raise _Failure("error", "Response exceeds the download byte limit.", response.status)
        if decoder is not None:
            try:
                output = decoder.decompress(chunk, max_bytes - len(body) + 1)
            except zlib.error:
                raise _Failure("error", "Invalid compressed response.", response.status) from None
            body.extend(output)
            if len(body) > max_bytes or decoder.unconsumed_tail:
                raise _Failure("error", "Decompressed response exceeds the byte limit.", response.status)
            if decoder.unused_data:
                raise _Failure("error", "Trailing or concatenated compressed data is not accepted.", response.status)
        else:
            body.extend(chunk)
    if expected is not None and downloaded != expected:
        raise _Failure("error", "Incomplete HTTP response body.", response.status)
    if decoder is not None and not decoder.eof:
        raise _Failure("error", "Incomplete compressed response.", response.status)
    deadline.remaining()
    return bytes(body)


def _wire_request(target: _Target, addresses: tuple[_Address, ...], deadline: _Deadline, max_bytes: int) -> _Response:
    sock = None
    response = None
    try:
        sock = _connect(target, addresses, deadline)
        request = ("GET " + target.request_target + " HTTP/1.1\r\nHost: " + target.host_header +
                   "\r\nUser-Agent: " + _USER_AGENT + "\r\nAccept: text/html, application/json, text/plain, */*;q=0.5" +
                   "\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n")
        sock.settimeout(deadline.remaining())
        sock.sendall(request.encode("ascii"))
        deadline.remaining()
        reader = _DeadlineReader(sock, deadline)
        # HTTPResponse only calls makefile('rb'); the stdlib stub is narrower
        # than its intentionally duck-typed runtime socket contract.
        response = http.client.HTTPResponse(_ResponseSocket(reader))  # type: ignore[arg-type]
        response.begin()
        deadline.remaining()
        if response.headers.defects:
            raise _Failure("error", "Malformed HTTP response headers.", response.status)
        # Buffered prefetch is already counted. Limit all subsequent bytes,
        # including chunk framing, independently of the decoded body limit.
        reader.limit = _MAX_HEADER_BYTES + max_bytes
        # Never download redirect/error pages, log in or solve a challenge.
        content = _read_body(response, deadline, max_bytes) if 200 <= response.status < 300 else b""
        return _Response(response.status, response.headers, content)
    except TimeoutError:
        raise _Failure("error", "Network timeout exceeded.") from None
    except (http.client.HTTPException, OSError, ValueError):
        raise _Failure("error", "Network request failed or HTTP response was malformed.") from None
    finally:
        if response is not None:
            response.close()
        _close(sock)


def _redirect_target(current: _Target, response: _Response) -> _Target:
    locations = response.headers.get_all("Location", [])
    if len(locations) != 1 or not locations[0]:
        raise _Failure("error", "Redirect has no unique Location header.", response.status)
    location = locations[0]
    # urljoin/urlsplit strip some dangerous whitespace; reject it beforehand.
    if len(location) > _MAX_URL_BYTES or _unsafe_characters(location):
        raise _Failure("blocked", "Unsafe redirect Location.", response.status)
    try:
        target = _validate_url(urljoin(current.url, location))
    except ValueError:
        raise _Failure("blocked", "Malformed redirect Location.", response.status) from None
    if current.scheme == "https" and target.scheme != "https":
        raise _Failure("blocked", "HTTPS-to-HTTP redirects are not followed.", response.status)
    path = urlsplit(target.url).path.lower()
    if re.search(r"(?:^|/)(?:login|log-in|signin|sign-in|sso)(?:/|$)|/(?:oauth|oauth2)/authorize(?:/|$)", path):
        raise _Failure("needs_credentials", "Redirect requires authentication; no login was attempted.", response.status)
    return target


def _robots_normalize(value: str) -> str:
    value = quote(value, safe="%/:?@!$&'()*+,;=-._~")

    def normalize(match) -> str:
        character = chr(int(match.group(1), 16))
        return character if character in _UNRESERVED else "%" + match.group(1).upper()

    return re.sub(r"%([0-9a-fA-F]{2})", normalize, value)


def _rule_matches(pattern: str, path: str) -> bool:
    """REP '*' and terminal '$' without catastrophic regex backtracking."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    if "*" not in pattern:
        return path == pattern if anchored else path.startswith(pattern)
    pieces = pattern.split("*")
    if not path.startswith(pieces[0]):
        return False
    offset = len(pieces[0])
    for piece in pieces[1:-1]:
        found = path.find(piece, offset)
        if found < 0:
            return False
        offset = found + len(piece)
    if anchored:
        return path.endswith(pieces[-1]) and len(path) - len(pieces[-1]) >= offset
    return path.find(pieces[-1], offset) >= 0


@dataclass(frozen=True)
class _RobotsPolicy:
    rules: tuple[tuple[bool, str, int], ...] = ()
    delay: float = 0.0
    failure: str = ""

    def permits(self, target: _Target, deadline: _Deadline) -> bool:
        if self.failure:
            return False
        path = _robots_normalize(target.request_target)
        longest = -1
        allowed = True
        for allow, pattern, specificity in self.rules:
            deadline.remaining()
            if specificity >= longest and _rule_matches(pattern, path):
                if specificity > longest:
                    longest, allowed = specificity, allow
                else:
                    allowed = allowed or allow
        return allowed


def _parse_robots(body: bytes, headers: Message, deadline: _Deadline) -> _RobotsPolicy:
    media_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if media_type and media_type not in {"text/plain", "application/octet-stream"}:
        raise _Failure("robots_denied", "robots.txt did not return a plain-text policy.")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise _Failure("robots_denied", "robots.txt is not valid UTF-8 text.") from None
    groups = []
    agents = []
    rules = []
    delay = 0.0
    has_directives = False
    recognized = False
    meaningful = False
    total_rules = 0

    def finish() -> None:
        nonlocal agents, rules, delay, has_directives
        if agents:
            groups.append((agents, rules, delay))
        agents, rules, delay, has_directives = [], [], 0.0, False

    for raw_line in text.splitlines():
        deadline.remaining()
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            # A blank/comment line does not terminate a REP group.
            continue
        meaningful = True
        if len(line) > _MAX_URL_BYTES or ":" not in line or "\x00" in line:
            raise _Failure("robots_denied", "robots.txt contains malformed policy text.")
        field, value = (part.strip() for part in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            recognized = True
            if has_directives:
                finish()
            if not re.fullmatch(r"[A-Za-z0-9_*./-]+", value):
                raise _Failure("robots_denied", "robots.txt has an invalid user-agent group.")
            agents.append(value.lower())
        elif field in {"allow", "disallow"}:
            recognized = True
            if not agents:
                raise _Failure("robots_denied", "robots.txt has a rule without a user-agent group.")
            has_directives = True
            if not value:
                continue
            if not value.startswith("/"):
                raise _Failure("robots_denied", "robots.txt has a malformed path rule.")
            pattern = _robots_normalize(value)
            measured = pattern[:-1] if pattern.endswith("$") else pattern
            specificity = len(re.sub(r"%[0-9A-F]{2}", "x", measured.replace("*", "")))
            rules.append((field == "allow", pattern, specificity))
            total_rules += 1
            if total_rules > _MAX_ROBOTS_RULES:
                raise _Failure("robots_denied", "robots.txt exceeds the rule limit.")
        elif field in {"crawl-delay", "request-rate"}:
            recognized = True
            if not agents:
                raise _Failure("robots_denied", "robots.txt has pacing outside a user-agent group.")
            has_directives = True
            try:
                if field == "crawl-delay":
                    seconds = float(value)
                else:
                    count, period = (float(part) for part in value.split("/"))
                    if not math.isfinite(count) or not math.isfinite(period) or count <= 0 or period <= 0:
                        raise ValueError
                    seconds = period / count
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError
            except (ValueError, OverflowError):
                raise _Failure("robots_denied", "robots.txt has an invalid rate or crawl delay.") from None
            delay = max(delay, seconds)
        elif field in {"sitemap", "host", "clean-param"}:
            recognized = True
        # Unknown extension fields do not invalidate otherwise valid REP rules.
    finish()
    if meaningful and not recognized:
        raise _Failure("robots_denied", "Response could not be recognized as robots.txt.")
    selected = []
    best = -1
    for group_agents, group_rules, group_delay in groups:
        deadline.remaining()
        score = max((0 if agent == "*" else len(agent) for agent in group_agents
                     if agent == "*" or agent in _ROBOT_TOKEN.lower()), default=-1)
        if score > best:
            selected, best = [(group_rules, group_delay)], score
        elif score == best and score >= 0:
            selected.append((group_rules, group_delay))
    if best < 0:
        return _RobotsPolicy()
    return _RobotsPolicy(tuple(rule for group_rules, _ in selected for rule in group_rules),
                         max((delay for _, delay in selected), default=0.0))


def _decode_text(content: bytes, headers: Message) -> str:
    charset = headers.get_content_charset() or "utf-8"
    try:
        return content.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return content.decode("utf-8", errors="replace")


def _browser_challenge(text: str, content_type: str) -> bool:
    if content_type and "html" not in content_type:
        return False
    sample = text[:200000].lower()
    return any(marker in sample for marker in (
        "/cdn-cgi/challenge-platform/", "cf-chl-", '<title>just a moment',
        '<title>verify you are human', '<title>checking your browser',
        '<title>captcha', 'id="challenge-form"', "id='challenge-form'",
    ))


class SafeFetcher:
    """Fetch public URLs without credentials; return the documented plain dict.

    max_bytes bounds each encoded and decoded body, including robots (which has
    an additional 512 KB ceiling). No partial body is returned on failure. The
    timeout is shared by all requests in a fetch, not restarted on redirects.
    Successful robots policies cache for 15 minutes, uncertain ones for 1 minute.
    Cache and origin pacing state are memory-only and bounded to 128 origins.
    """

    def __init__(self, timeout: float = 12, max_bytes: int = 4000000):
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self.timeout = float(timeout)
        self.max_bytes = max_bytes
        self._robots_cache: OrderedDict = OrderedDict()
        self._last_request: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def _cached_policy(self, origin) -> _RobotsPolicy | None:
        with self._lock:
            cached = self._robots_cache.get(origin)
            if cached is not None:
                expires, policy = cached
                if expires > time.monotonic():
                    self._robots_cache.move_to_end(origin)
                    return policy
                del self._robots_cache[origin]
        return None

    def _cache_policy(self, origin, policy: _RobotsPolicy) -> None:
        ttl = _ROBOTS_FAILURE_CACHE_SECONDS if policy.failure else _ROBOTS_CACHE_SECONDS
        with self._lock:
            self._robots_cache[origin] = (time.monotonic() + ttl, policy)
            self._robots_cache.move_to_end(origin)
            while len(self._robots_cache) > _MAX_CACHED_ORIGINS:
                self._robots_cache.popitem(last=False)

    def _pace(self, target: _Target, deadline: _Deadline) -> None:
        policy = self._cached_policy(target.origin)
        interval = max(_MIN_INTERVAL, policy.delay if policy is not None else 0.0)
        with self._lock:
            now = time.monotonic()
            last = self._last_request.get(target.origin)
            delay = max(0.0, last + interval - now) if last is not None else 0.0
            if delay >= deadline.remaining():
                raise _Failure("error", "Total request timeout cannot accommodate the origin's crawl delay.")
            self._last_request[target.origin] = now + delay
            self._last_request.move_to_end(target.origin)
            while len(self._last_request) > _MAX_CACHED_ORIGINS:
                self._last_request.popitem(last=False)
        if delay:
            time.sleep(delay)
        deadline.remaining()

    def _request(self, target: _Target, addresses: tuple[_Address, ...], deadline: _Deadline, max_bytes: int) -> _Response:
        self._pace(target, deadline)
        deadline.request()
        return _wire_request(target, addresses, deadline, max_bytes)

    def _load_robots(self, target: _Target, deadline: _Deadline, loading: frozenset = frozenset()) -> _RobotsPolicy:
        cached = self._cached_policy(target.origin)
        if cached is not None:
            return cached
        if target.origin in loading:
            raise _Failure("robots_denied", "Cross-origin robots policy redirect cycle.")
        loading = loading | {target.origin}
        origin = target.origin
        current = _validate_url(target.origin_url + "/robots.txt")
        seen = set()
        try:
            while True:
                deadline.remaining()
                if current.url in seen:
                    raise _Failure("robots_denied", "robots.txt redirect loop.")
                seen.add(current.url)
                addresses = _resolve_addresses(current, deadline)
                if current.origin != origin:
                    other = self._load_robots(current, deadline, loading)
                    if not other.permits(current, deadline):
                        raise _Failure("robots_denied", "Redirected robots.txt is prohibited by its destination's policy.")
                reply = self._request(current, addresses, deadline, min(self.max_bytes, _ROBOTS_MAX_BYTES))
                if reply.status in _REDIRECTS:
                    deadline.redirect()
                    current = _redirect_target(current, reply)
                    continue
                if reply.status == 404:
                    policy = _RobotsPolicy()
                elif reply.status in {200, 204}:
                    if reply.headers.get("Content-Range"):
                        raise _Failure("robots_denied", "Partial robots.txt responses are not accepted.")
                    policy = _parse_robots(reply.content, reply.headers, deadline)
                else:
                    raise _Failure("robots_denied", "robots.txt could not be verified (HTTP " + str(reply.status) + ").")
                break
        except _Failure as exc:
            policy = _RobotsPolicy(failure="Robots policy could not be verified: " + exc.message)
        except (OSError, http.client.HTTPException, ValueError):
            policy = _RobotsPolicy(failure="Robots policy could not be verified because of a network or policy error.")
        self._cache_policy(origin, policy)
        return policy

    def fetch(self, url, respect_robots=True) -> dict:
        result = {"status": "error", "url": "", "http_status": None, "content": b"", "text": "", "content_type": "", "diagnostics": []}
        deadline = _Deadline(self.timeout)
        seen = set()
        try:
            if not isinstance(respect_robots, bool):
                raise _Failure("error", "respect_robots must be a boolean.")
            target = _validate_url(url)
            while True:
                deadline.remaining()
                if target.url in seen:
                    raise _Failure("error", "Redirect loop detected.")
                seen.add(target.url)
                result["url"] = target.url
                result["http_status"] = None
                # Resolve before robots, but connect with THESE numeric answers
                # afterwards, even if DNS changes while fetching the policy.
                addresses = _resolve_addresses(target, deadline)
                if respect_robots:
                    policy = self._load_robots(target, deadline)
                    if policy.failure:
                        raise _Failure("robots_denied", policy.failure)
                    if not policy.permits(target, deadline):
                        raise _Failure("robots_denied", "robots.txt prohibits this URL for " + _ROBOT_TOKEN + ".")
                reply = self._request(target, addresses, deadline, self.max_bytes)
                result["http_status"] = reply.status
                if reply.status in _REDIRECTS:
                    deadline.redirect()
                    target = _redirect_target(target, reply)
                    result["diagnostics"].append("Following validated HTTP redirect (" + str(reply.status) + ").")
                    continue
                if reply.status in {401, 407}:
                    raise _Failure("needs_credentials", "HTTP authentication is required; no credentials were sent.", reply.status)
                if reply.status == 403:
                    raise _Failure("blocked", "HTTP 403: access forbidden; no bypass was attempted.", reply.status)
                if reply.status == 429:
                    raise _Failure("blocked", "HTTP 429: rate limited; no automatic retry was attempted.", reply.status)
                if reply.status == 206 or reply.headers.get("Content-Range"):
                    raise _Failure("error", "Partial HTTP responses are not accepted.", reply.status)
                if not 200 <= reply.status < 300:
                    raise _Failure("error", "HTTP request failed (" + str(reply.status) + ").", reply.status)
                text = _decode_text(reply.content, reply.headers)
                content_type = reply.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if _browser_challenge(text, content_type):
                    raise _Failure("needs_browser", "Browser or CAPTCHA challenge detected; no bypass was attempted.", reply.status)
                deadline.remaining()
                result.update(status="ok", content=reply.content, text=text, content_type=content_type)
                return result
        except _Failure as exc:
            result["status"] = exc.status
            if exc.http_status is not None:
                result["http_status"] = exc.http_status
            result["diagnostics"].append(exc.message)
        except (OSError, http.client.HTTPException, ValueError):
            result["diagnostics"].append("Network request failed; sensitive exception details were omitted.")
        return result
