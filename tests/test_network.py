"""Offline safety tests: no test opens a real network connection."""

import io
import socket
import ssl
import threading
import time
import unittest
from email.message import Message
from unittest.mock import Mock, patch

from korean_job_search import network


PUBLIC_IP = "93.184.216.34"
BASE = "https://example.com"


def dns_answer(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, port))]


def response(status=200, body=b"hello", **headers):
    message = Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return network._Response(status, message, body)


class MemorySocket:
    """An in-memory raw socket, also usable as the result of a TLS wrap."""

    def __init__(self, payload, peer=(PUBLIC_IP, 443), chunk_size=8192, on_recv=None):
        self.stream = io.BytesIO(payload)
        self.peer = peer
        self.chunk_size = chunk_size
        self.on_recv = on_recv
        self.sent = bytearray()
        self.connected = None
        self.closed = False
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected = address

    def getpeername(self):
        return self.peer

    def do_handshake(self):
        pass

    def recv_into(self, buffer):
        if self.on_recv:
            self.on_recv()
        data = self.stream.read(min(len(buffer), self.chunk_size))
        buffer[:len(data)] = data
        return len(data)

    def sendall(self, data):
        self.sent.extend(data)

    def close(self):
        self.closed = True


class FetcherTests(unittest.TestCase):
    def setUp(self):
        self.dns = patch.object(network.socket, "getaddrinfo", side_effect=dns_answer).start()
        patch.object(network, "_MIN_INTERVAL", 0.0).start()
        self.routes = {}
        self.calls = []
        self.wire = patch.object(network, "_wire_request", side_effect=self.transport).start()
        self.addCleanup(patch.stopall)
        self.fetcher = network.SafeFetcher()

    def transport(self, target, addresses, deadline, max_bytes):
        self.calls.append(target.url)
        self.assertTrue(addresses)
        self.assertGreater(deadline.remaining(), 0)
        if target.url not in self.routes:
            raise AssertionError("Unexpected request: " + target.url)
        value = self.routes[target.url]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def allow(self, origin=BASE):
        self.routes[origin + "/robots.txt"] = response(404, b"")

    def test_result_contract_and_robots_404(self):
        self.allow()
        self.routes[BASE + "/jobs"] = response(body="채용".encode(), Content_Type="text/html; charset=utf-8")
        result = self.fetcher.fetch(BASE + "/jobs")
        self.assertEqual(set(result), {"status", "url", "http_status", "content", "text", "content_type", "diagnostics"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["content"], "채용".encode())
        self.assertEqual(result["text"], "채용")
        self.assertEqual(self.calls, [BASE + "/robots.txt", BASE + "/jobs"])

    def test_invalid_urls_are_never_requested_or_echoed(self):
        urls = [
            "file:///etc/passwd", "ftp://example.com/file", "http://user:password@example.com/",
            "https://@example.com/", "https://example.com:444/", "http://example.com:443/",
            "http://example.com:/", "http://example.com:080/", "https://example.com\\@evil.com/",
            "https://example.com/\r\nX-Secret: secret", " https://example.com", "https://example.com/\x00",
            "https://example.com/%0d%0aX-secret", "https://example.com/%5cpath", "https://example.com/%ZZ",
            "https://%31%32%37.0.0.1/", "https://example..com/", "https://-example.com/",
            "https://example.com/#\x7f", "https://example.com/?access_token=supersecret",
            "https://example.com/?%61pi_key=supersecret", "https://[fe80::1%25en0]/", "http:///missing-host",
            "http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://localhost/",
            "http://foo.localhost./", "http://host.local/", "http://metadata.google.internal/",
        ]
        for url in urls:
            with self.subTest(url=url):
                result = self.fetcher.fetch(url)
                self.assertIn(result["status"], {"blocked", "unsupported", "needs_credentials"})
                self.assertEqual(result["content"], b"")
                self.assertNotIn("supersecret", repr(result))
                self.assertNotIn("user:password", repr(result))
        self.assertEqual(self.calls, [])
        self.dns.assert_not_called()

    def test_credential_query_aliases_are_blocked_without_echo_or_network(self):
        for key in ("session_id", "authToken", "auth%2dtoken"):
            with self.subTest(key=key):
                result = self.fetcher.fetch(BASE + "/jobs?" + key + "=supersecret")
                self.assertEqual(result["status"], "needs_credentials")
                self.assertNotIn("supersecret", repr(result))
        self.assertEqual(self.calls, [])
        self.dns.assert_not_called()


    def test_non_public_literals(self):
        addresses = ["127.0.0.1", "0.0.0.0", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1", "192.0.0.8", "192.88.99.1", "192.0.2.1", "198.18.0.1", "198.51.100.1", "203.0.113.1", "224.0.0.1", "255.255.255.255", "[::1]", "[::]", "[fc00::1]", "[fe80::1]", "[ff02::1]", "[::ffff:127.0.0.1]", "[64:ff9b::7f00:1]", "[2002:7f00:1::]", "[2001:db8::1]", "[3fff::1]"]
        for address in addresses:
            with self.subTest(address=address):
                self.assertEqual(self.fetcher.fetch("http://" + address)["status"], "blocked")
        self.assertEqual(self.calls, [])
        self.dns.assert_not_called()

    def test_public_literals_and_default_ports(self):
        for url in ["http://93.184.216.34:80/jobs", "https://[2606:4700:4700::1111]:443/jobs", BASE + ":443/jobs"]:
            with self.subTest(url=url):
                target = network._validate_url(url)
                self.routes[target.url] = response()
                self.assertEqual(self.fetcher.fetch(url, respect_robots=False)["status"], "ok")

    def test_mixed_dns_and_malformed_dns_are_rejected(self):
        public = dns_answer("example.com", 443)
        for answer in [public + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))], [], [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 443))], [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", 443, 0, 7))]]:
            with self.subTest(answer=answer):
                self.dns.side_effect = None
                self.dns.return_value = answer
                result = self.fetcher.fetch(BASE, respect_robots=False)
                self.assertIn(result["status"], {"blocked", "error"})
        self.assertEqual(self.calls, [])

    def test_dns_failure_does_not_leak_exception(self):
        self.dns.side_effect = socket.gaierror("secret-token DNS provider internal details")
        result = self.fetcher.fetch(BASE)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret-token", repr(result))

    def test_private_and_credential_redirects_are_not_followed(self):
        self.allow()
        for location, status in [("http://127.0.0.1/", "blocked"), ("https://10.0.0.1/", "blocked"), ("https://u:secret@example.com/", "blocked"), ("https://example.com:8443/", "blocked"), ("file:///etc/passwd", "unsupported"), ("/login?next=/jobs", "needs_credentials"), ("/jobs?session_id=supersecret", "needs_credentials")]:
            with self.subTest(location=location):
                self.routes[BASE + "/jobs"] = response(302, b"", Location=location)
                result = self.fetcher.fetch(BASE + "/jobs")
                self.assertEqual(result["status"], status)
                self.assertNotIn("u:secret", repr(result))
        self.assertTrue(all(url in [BASE + "/robots.txt", BASE + "/jobs"] for url in self.calls))

    def test_redirect_dns_is_validated_before_connecting(self):
        self.allow()
        self.routes[BASE + "/jobs"] = response(302, b"", Location="https://evil.example.com/internal")
        self.dns.side_effect = lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3" if host == "evil.example.com" else PUBLIC_IP, port))]
        result = self.fetcher.fetch(BASE + "/jobs")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.calls, [BASE + "/robots.txt", BASE + "/jobs"])

    def test_cross_origin_redirect_obeys_new_robots(self):
        self.allow()
        self.routes[BASE + "/jobs"] = response(302, b"", Location="https://other.example.com/private")
        self.routes["https://other.example.com/robots.txt"] = response(body=b"User-agent: *\nDisallow: /private")
        result = self.fetcher.fetch(BASE + "/jobs")
        self.assertEqual(result["status"], "robots_denied")
        self.assertNotIn("https://other.example.com/private", self.calls)

    def test_allowed_cross_origin_and_relative_redirects(self):
        self.allow()
        self.allow("https://other.example.com")
        self.routes[BASE + "/jobs"] = response(301, b"", Location="/careers?lang=ko")
        self.routes[BASE + "/careers?lang=ko"] = response(307, b"", Location="https://other.example.com/list")
        self.routes["https://other.example.com/list"] = response(body=b"jobs")
        result = self.fetcher.fetch(BASE + "/jobs")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["url"], "https://other.example.com/list")
        self.assertEqual(self.calls.count(BASE + "/robots.txt"), 1)
        self.assertIn("https://other.example.com/robots.txt", self.calls)

    def test_redirect_loops_and_limits(self):
        self.allow()
        self.routes[BASE + "/loop"] = response(302, b"", Location="/loop")
        self.assertEqual(self.fetcher.fetch(BASE + "/loop")["status"], "error")
        for number in range(network._MAX_REDIRECTS + 2):
            self.routes[BASE + "/" + str(number)] = response(302, b"", Location="/" + str(number + 1))
        result = self.fetcher.fetch(BASE + "/0")
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("redirect" in item.lower() for item in result["diagnostics"]))

    def test_https_downgrade_and_unsafe_location(self):
        for location in ["http://example.com/jobs", "\t/private", "https://example.com/\npath", "\\evil.example.com/x", ""]:
            self.routes[BASE + "/jobs"] = response(302, b"", Location=location)
            result = self.fetcher.fetch(BASE + "/jobs", respect_robots=False)
            self.assertIn(result["status"], {"blocked", "error"})

    def test_robots_cache_and_respect_override(self):
        self.routes[BASE + "/robots.txt"] = response(body=b"User-agent: *\nDisallow: /blocked")
        self.routes[BASE + "/jobs"] = response()
        self.routes[BASE + "/blocked"] = response()
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")
        self.assertEqual(self.fetcher.fetch(BASE + "/blocked")["status"], "robots_denied")
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")
        self.assertEqual(self.calls.count(BASE + "/robots.txt"), 1)
        self.assertEqual(self.fetcher.fetch(BASE + "/blocked", respect_robots=False)["status"], "ok")
        self.assertEqual(self.fetcher.fetch("http://127.0.0.1", respect_robots=False)["status"], "blocked")

    def test_robots_fail_closed(self):
        failures = [response(401), response(403), response(429), response(500), response(206), response(body=b"<html>sign in</html>", Content_Type="text/html"), response(body=b"not a robots file"), response(body=b"User-agent: *\nDisallow: missing-slash"), response(body=b"User-agent: *\nCrawl-delay: nonsense"), network._Failure("error", "Network timeout.")]
        for failure in failures:
            with self.subTest(failure=failure):
                self.fetcher = network.SafeFetcher()
                self.routes[BASE + "/robots.txt"] = failure
                result = self.fetcher.fetch(BASE + "/jobs")
                self.assertEqual(result["status"], "robots_denied")
        self.assertNotIn(BASE + "/jobs", self.calls)

    def test_robots_wildcards_specific_agents_allow_ties_and_query(self):
        self.routes[BASE + "/robots.txt"] = response(body=b"User-agent: *\nDisallow: /\n\nUser-agent: KoreanJobSearch\nDisallow: /private*\nAllow: /private/open\nDisallow: /*?topic=*\nDisallow: /equal\nAllow: /equal\nDisallow: /end$\n\nUser-agent: KoreanJobSearch\nDisallow: /merged\n")
        for path, expected in [("/jobs", "ok"), ("/private", "robots_denied"), ("/private/open", "ok"), ("/jobs?topic=yes", "robots_denied"), ("/equal", "ok"), ("/end", "robots_denied"), ("/ending", "ok"), ("/merged", "robots_denied")]:
            self.routes[BASE + path] = response()
            with self.subTest(path=path):
                self.assertEqual(self.fetcher.fetch(BASE + path)["status"], expected)

    def test_robots_percent_and_unicode_paths(self):
        self.routes[BASE + "/robots.txt"] = response(body="User-agent: *\nDisallow: /채용/비공개\nDisallow: /private\nDisallow: /literal%2A\n".encode())
        for path in ["/채용/비공개", "/%70rivate", "/literal%2a"]:
            with self.subTest(path=path):
                self.assertEqual(self.fetcher.fetch(BASE + path)["status"], "robots_denied")

    def test_empty_robots_and_other_bot_groups_allow(self):
        for body in [b"", b"# Nothing restricted\n", b"Sitemap: https://example.com/map.xml", b"User-agent: OtherBot\nDisallow: /\n"]:
            self.fetcher = network.SafeFetcher()
            self.routes[BASE + "/robots.txt"] = response(body=body)
            self.routes[BASE + "/jobs"] = response()
            self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")

    def test_robots_redirect_private_denied(self):
        self.routes[BASE + "/robots.txt"] = response(302, b"", Location="https://127.0.0.1/robots.txt")
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "robots_denied")
        self.assertEqual(self.calls, [BASE + "/robots.txt"])

    def test_robots_cross_origin_redirect_checks_destination_rules(self):
        self.routes[BASE + "/robots.txt"] = response(302, b"", Location="https://other.example.com/policy.txt")
        self.routes["https://other.example.com/robots.txt"] = response(body=b"User-agent: *\nDisallow: /policy.txt")
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "robots_denied")
        self.assertEqual(self.calls, [BASE + "/robots.txt", "https://other.example.com/robots.txt"])

    def test_robots_cross_origin_recursion_is_bounded(self):
        self.routes[BASE + "/robots.txt"] = response(302, b"", Location="https://other.example.com/policy.txt")
        self.routes["https://other.example.com/robots.txt"] = response(302, b"", Location=BASE + "/policy.txt")
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "robots_denied")
        self.assertLessEqual(len(self.calls), 2)

    def test_http_access_and_browser_statuses(self):
        for reply, status in [(response(401), "needs_credentials"), (response(407), "needs_credentials"), (response(403), "blocked"), (response(429, Retry_After="9999-secret"), "blocked"), (response(500), "error"), (response(404), "error"), (response(206), "error"), (response(body=b"<html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script></html>", Content_Type="text/html"), "needs_browser")]:
            with self.subTest(status=status, reply=reply.status):
                self.routes[BASE + "/jobs"] = reply
                result = self.fetcher.fetch(BASE + "/jobs", respect_robots=False)
                self.assertEqual(result["status"], status)
                self.assertNotIn("9999-secret", repr(result["diagnostics"]))

    def test_declared_charset_and_unknown_charset(self):
        for charset, body in [("euc-kr", "채용".encode("euc-kr")), ("not-a-charset", "채용".encode())]:
            self.routes[BASE + "/jobs"] = response(body=body, Content_Type="text/html; charset=" + charset)
            self.assertEqual(self.fetcher.fetch(BASE + "/jobs", respect_robots=False)["text"], "채용")

    def test_robots_crawl_delay_respects_shared_deadline(self):
        self.routes[BASE + "/robots.txt"] = response(body=b"User-agent: *\nCrawl-delay: 60\n")
        result = network.SafeFetcher(timeout=0.1).fetch(BASE + "/jobs")
        self.assertEqual(result["status"], "error")
        self.assertNotIn(BASE + "/jobs", self.calls)

    def test_cache_expiry_refetches_policy(self):
        self.routes[BASE + "/robots.txt"] = [response(404, b""), response(body=b"User-agent: *\nDisallow: /")]
        self.routes[BASE + "/jobs"] = response()
        with patch.object(network, "_ROBOTS_CACHE_SECONDS", 0):
            self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")
            self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "robots_denied")
        self.assertEqual(self.calls.count(BASE + "/robots.txt"), 2)

    def test_robots_partial_and_invalid_rates_fail_closed(self):
        replies = [response(body=b"User-agent: *\nAllow: /", Content_Range="bytes 0-22/100")]
        replies += [response(body=("User-agent: *\nRequest-rate: " + rate).encode()) for rate in ["inf/1", "1/0", "nan/1", "1/inf", "0/10", "-1/10"]]
        for reply in replies:
            with self.subTest(reply=reply):
                self.routes[BASE + "/robots.txt"] = reply
                result = network.SafeFetcher().fetch(BASE + "/jobs")
                self.assertEqual(result["status"], "robots_denied")
        self.assertNotIn(BASE + "/jobs", self.calls)

    def test_valid_request_rate_is_honored(self):
        self.routes[BASE + "/robots.txt"] = response(body=b"User-agent: *\nRequest-rate: 1/60")
        result = network.SafeFetcher(timeout=0.1).fetch(BASE + "/jobs")
        self.assertEqual(result["status"], "error")
        self.assertNotIn(BASE + "/jobs", self.calls)

    def test_cached_robots_never_skip_fresh_dns_validation(self):
        self.allow()
        self.routes[BASE + "/jobs"] = response()
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")
        self.dns.side_effect = lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "blocked")
        self.assertEqual(self.calls.count(BASE + "/jobs"), 1)

    def test_page_keeps_validated_ip_while_robots_dns_changes(self):
        self.allow()
        self.routes[BASE + "/jobs"] = response()
        self.dns.side_effect = [dns_answer("example.com", 443), [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]]
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")
        robots_addresses = self.wire.call_args_list[0].args[1]
        page_addresses = self.wire.call_args_list[1].args[1]
        self.assertEqual(robots_addresses[0].sockaddr[0], "1.1.1.1")
        self.assertEqual(page_addresses[0].sockaddr[0], PUBLIC_IP)
        self.assertEqual(self.dns.call_count, 2)

    def test_redirects_share_total_deadline(self):
        clock = [100.0]
        self.routes[BASE + "/0"] = response(302, b"", Location="/1")
        self.routes[BASE + "/1"] = response(302, b"", Location="/2")
        self.routes[BASE + "/2"] = response()

        def slow_transport(*args):
            reply = self.transport(*args)
            clock[0] += 0.3
            return reply

        self.wire.side_effect = slow_transport
        with patch.object(network.time, "monotonic", side_effect=lambda: clock[0]):
            result = network.SafeFetcher(timeout=0.5).fetch(BASE + "/0", respect_robots=False)
        self.assertEqual(result["status"], "error")
        self.assertNotIn(BASE + "/2", self.calls)
        self.assertTrue(any("timeout" in item.lower() for item in result["diagnostics"]))

    def test_robots_network_failure_is_cached_and_closed(self):
        self.routes[BASE + "/robots.txt"] = network._Failure("error", "Network timeout.")
        for _ in range(2):
            self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "robots_denied")
        self.assertEqual(self.calls, [BASE + "/robots.txt"])

    def test_robots_same_origin_redirect_allowed_and_requests_bounded(self):
        self.routes[BASE + "/robots.txt"] = response(302, b"", Location="/policy.txt")
        self.routes[BASE + "/policy.txt"] = response(body=b"User-agent: *\nAllow: /")
        self.routes[BASE + "/jobs"] = response()
        self.assertEqual(self.fetcher.fetch(BASE + "/jobs")["status"], "ok")
        self.assertEqual(self.calls, [BASE + "/robots.txt", BASE + "/policy.txt", BASE + "/jobs"])
        with patch.object(network, "_MAX_REQUESTS", 1):
            self.assertEqual(network.SafeFetcher().fetch(BASE + "/jobs")["status"], "robots_denied")

    def test_constructor_validation(self):
        for options in [{"timeout": 0}, {"timeout": -1}, {"timeout": float("inf")}, {"timeout": float("nan")}, {"timeout": True}, {"max_bytes": 0}, {"max_bytes": 1.1}, {"max_bytes": True}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    network.SafeFetcher(**options)


class WireTests(unittest.TestCase):
    def setUp(self):
        patch.object(network, "_MIN_INTERVAL", 0.0).start()
        self.dns = patch.object(network.socket, "getaddrinfo", side_effect=dns_answer).start()
        self.addCleanup(patch.stopall)

    def fetch_wire(self, payload, max_bytes=4000000, sock=None, url="http://example.com/jobs", **kwargs):
        sock = sock or MemorySocket(payload, peer=(PUBLIC_IP, 80))
        with patch.object(network.socket, "socket", return_value=sock):
            result = network.SafeFetcher(max_bytes=max_bytes, **kwargs).fetch(url, respect_robots=False)
        return result, sock

    def test_pinned_socket_host_no_proxies_or_second_dns(self):
        with patch.dict("os.environ", {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1"}), patch.object(network.socket, "create_connection", side_effect=AssertionError("Unpinned connection")):
            result, sock = self.fetch_wire(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\njobs")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], b"jobs")
        self.assertEqual(sock.connected, (PUBLIC_IP, 80))
        self.assertEqual(self.dns.call_count, 1)
        self.assertIn(b"Host: example.com\r\n", sock.sent)
        self.assertIn(b"Accept-Encoding: identity\r\n", sock.sent)
        self.assertIn(network._USER_AGENT.encode(), sock.sent)
        self.assertNotIn(b"Authorization:", sock.sent)
        self.assertNotIn(b"Cookie:", sock.sent)
        self.assertTrue(sock.closed)

    def test_dns_rebinding_cannot_change_pinned_endpoint(self):
        self.dns.side_effect = [dns_answer("example.com", 80), [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]]
        result, sock = self.fetch_wire(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(sock.connected, (PUBLIC_IP, 80))
        self.assertEqual(self.dns.call_count, 1)

    def test_peer_mismatch_aborts_before_request(self):
        sock = MemorySocket(b"", peer=("127.0.0.1", 80))
        result, _ = self.fetch_wire(b"", sock=sock)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(sock.sent, b"")
        self.assertTrue(sock.closed)

    def test_https_default_verification_sni_and_host(self):
        sock = MemorySocket(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        with patch.object(network.ssl, "create_default_context", return_value=context) as factory, patch.object(context, "wrap_socket", return_value=sock) as wrap:
            result, _ = self.fetch_wire(b"", sock=sock, url=BASE + "/jobs")
        self.assertEqual(result["status"], "ok")
        factory.assert_called_once_with()
        self.assertEqual(wrap.call_args.kwargs["server_hostname"], "example.com")
        self.assertFalse(wrap.call_args.kwargs["do_handshake_on_connect"])
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIn(b"Host: example.com\r\n", sock.sent)

    def test_tls_certificate_error_is_not_retried_insecurely(self):
        context = ssl.create_default_context()
        with patch.object(network.ssl, "create_default_context", return_value=context), patch.object(context, "wrap_socket", side_effect=ssl.SSLCertVerificationError("secret handshake metadata")) as wrap:
            result, sock = self.fetch_wire(b"", sock=MemorySocket(b""), url=BASE)
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("certificate" in item.lower() for item in result["diagnostics"]))
        self.assertNotIn("secret", repr(result))
        self.assertEqual(wrap.call_count, 1)
        self.assertEqual(sock.sent, b"")
        self.assertTrue(sock.closed)

    def test_size_limits_content_length_and_unannounced(self):
        for payload in [b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n", b"HTTP/1.1 200 OK\r\n\r\n12345", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n12345\r\n0\r\n\r\n"]:
            with self.subTest(payload=payload):
                result, sock = self.fetch_wire(payload, max_bytes=4)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["content"], b"")
                self.assertTrue(any("limit" in item.lower() for item in result["diagnostics"]))
                self.assertTrue(sock.closed)

    def test_exact_limit_and_chunked_response(self):
        for payload in [b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\njobs", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\njo\r\n2\r\nbs\r\n0\r\n\r\n"]:
            result, _ = self.fetch_wire(payload, max_bytes=4)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["content"], b"jobs")

    def test_incomplete_and_ambiguous_responses_fail(self):
        for payload in [b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\nsmall", b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n", b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nhi", b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", b"NOT HTTP\r\n\r\n", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\nanything"]:
            with self.subTest(payload=payload):
                result, _ = self.fetch_wire(payload)
                self.assertIn(result["status"], {"error", "unsupported"})
                self.assertEqual(result["content"], b"")

    def test_gzip_and_deflate_bounded_decompression(self):
        import gzip
        import zlib
        for encoding, body in [(b"gzip", gzip.compress(b"jobs")), (b"deflate", zlib.compress(b"jobs"))]:
            result, _ = self.fetch_wire(b"HTTP/1.1 200 OK\r\nContent-Encoding: " + encoding + b"\r\n\r\n" + body, max_bytes=100)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["content"], b"jobs")
        bomb = gzip.compress(b"X" * 100000)
        result, _ = self.fetch_wire(b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n" + bomb, max_bytes=1024)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["content"], b"")

    def test_invalid_compression_and_unsupported_encoding(self):
        import gzip
        for encoding, body in [(b"gzip", b"invalid"), (b"gzip", gzip.compress(b"jobs")[:-4]), (b"gzip", gzip.compress(b"jobs") + b"trailing"), (b"br", b"unsupported")]:
            result, _ = self.fetch_wire(b"HTTP/1.1 200 OK\r\nContent-Encoding: " + encoding + b"\r\n\r\n" + body)
            self.assertIn(result["status"], {"error", "unsupported"})

    def test_bounded_header_bytes(self):
        payload = b"HTTP/1.1 200 OK\r\n" + b"X-Long: " + b"a" * 40000 + b"\r\nY-Long: " + b"b" * 40000 + b"\r\n\r\n"
        result, _ = self.fetch_wire(payload)
        self.assertEqual(result["status"], "error")

    def test_slow_drip_headers_have_absolute_deadline(self):
        clock = [100.0]
        sock = MemorySocket(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\njobs", peer=(PUBLIC_IP, 80), chunk_size=1, on_recv=lambda: clock.__setitem__(0, clock[0] + 0.1))
        with patch.object(network.time, "monotonic", side_effect=lambda: clock[0]):
            result, _ = self.fetch_wire(b"", sock=sock, timeout=0.5)
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("timeout" in item.lower() for item in result["diagnostics"]))
        self.assertLess(len(sock.timeouts), 20)
        self.assertTrue(sock.closed)

    def test_slow_drip_body_has_absolute_deadline(self):
        clock = [100.0]
        body_offset = len(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n")
        sock = MemorySocket(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\njobs", peer=(PUBLIC_IP, 80), chunk_size=1)
        sock.on_recv = lambda: clock.__setitem__(0, clock[0] + (0.2 if sock.stream.tell() >= body_offset else 0.0))
        with patch.object(network.time, "monotonic", side_effect=lambda: clock[0]):
            result, _ = self.fetch_wire(b"", sock=sock, timeout=0.5)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["content"], b"")

    def test_ipv6_socket_pinning_and_bracketed_host(self):
        ip = "2606:4700:4700::1111"
        sock = MemorySocket(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n", peer=(ip, 80, 0, 0))
        result, _ = self.fetch_wire(b"", sock=sock, url="http://[" + ip + "]/jobs")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(sock.connected, (ip, 80, 0, 0))
        self.assertIn(("Host: [" + ip + "]\r\n").encode(), sock.sent)
        self.dns.assert_not_called()

    def test_connect_send_and_read_timeouts_close_sockets(self):
        for phase in ["connect", "sendall", "recv_into"]:
            with self.subTest(phase=phase):
                sock = MemorySocket(b"", peer=(PUBLIC_IP, 80))
                setattr(sock, phase, Mock(side_effect=TimeoutError("secret network details")))
                result, _ = self.fetch_wire(b"", sock=sock)
                self.assertEqual(result["status"], "error")
                self.assertNotIn("secret", repr(result))
                self.assertTrue(sock.closed)

    def test_dns_worker_capacity_is_bounded(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        try:
            with patch.object(network, "_DNS_SLOTS", semaphore), patch.object(network.socket, "socket") as create_socket:
                result = network.SafeFetcher().fetch(BASE, respect_robots=False)
            self.assertEqual(result["status"], "error")
            self.assertTrue(any("capacity" in item.lower() for item in result["diagnostics"]))
            self.dns.assert_not_called()
            create_socket.assert_not_called()
        finally:
            semaphore.release()

    def test_dns_timeout_is_bounded_and_cannot_connect_later(self):
        release = threading.Event()
        finished = threading.Event()

        def slow_dns(*args, **kwargs):
            try:
                release.wait(2)
                return dns_answer("example.com", 80)
            finally:
                finished.set()

        self.dns.side_effect = slow_dns
        start = time.monotonic()
        try:
            with patch.object(network.socket, "socket", side_effect=AssertionError("Late connection")) as create_socket:
                result = network.SafeFetcher(timeout=0.05).fetch("http://example.com", respect_robots=False)
                self.assertEqual(result["status"], "error")
                self.assertLess(time.monotonic() - start, 0.7)
                release.set()
                self.assertTrue(finished.wait(1))
                create_socket.assert_not_called()
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
