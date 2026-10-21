import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from weatherbot.errors import WeatherbotError
from weatherbot.http_client import HttpClient


class _QuietHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._reply()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.server.posted = self.rfile.read(length)  # type: ignore[attr-defined]
        self._reply()

    def _reply(self) -> None:
        status, raw = self.server.reply  # type: ignore[attr-defined]
        self.server.hits += 1  # type: ignore[attr-defined]
        body = raw.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _serve(address: str, reply: tuple[int, str]) -> tuple[ThreadingHTTPServer, int]:
    class _Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in address else socket.AF_INET

    server = _Server((address, 0), _QuietHandler)
    server.reply = reply  # type: ignore[attr-defined]
    server.hits = 0  # type: ignore[attr-defined]
    server.posted = b""  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, int(server.server_address[1])


def test_get_and_post_json_over_ipv4() -> None:
    server, port = _serve("127.0.0.1", (200, '{"ok": true}'))
    try:
        client = HttpClient(timeout=2, connect_timeout=2, attempts=1)
        assert client.get_json(f"http://127.0.0.1:{port}/events") == {"ok": True}
        assert client.post_json(f"http://127.0.0.1:{port}/books", {"token_id": "abc"}) == {"ok": True}
        assert json.loads(server.posted) == {"token_id": "abc"}
    finally:
        server.shutdown()


def test_http_status_is_not_retried() -> None:
    server, port = _serve("127.0.0.1", (503, "busy"))
    try:
        client = HttpClient(timeout=2, connect_timeout=2, attempts=3)
        with pytest.raises(WeatherbotError) as caught:
            client.get_json(f"http://127.0.0.1:{port}/events")
        assert "503" in caught.value.detail
        assert server.hits == 1
    finally:
        server.shutdown()


def test_connection_refused_names_the_address() -> None:
    client = HttpClient(timeout=1, connect_timeout=1, attempts=1)
    with pytest.raises(WeatherbotError) as caught:
        client.get_json("http://127.0.0.1:1/events")
    assert caught.value.source == "http"
    assert "127.0.0.1" in caught.value.detail
    assert "http://127.0.0.1:1/events" in caught.value.detail


def test_timeout_is_retried_then_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient(attempts=2)
    calls = {"n": 0}

    def fake_exchange(self: HttpClient, method: str, url: str, data: bytes | None, headers: dict[str, str]) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timed out")
        assert method == "GET"
        return '{"ok": 1}'

    monkeypatch.setattr(HttpClient, "_exchange", fake_exchange)
    assert client.get_json("https://gamma-api.polymarket.com/events") == {"ok": 1}
    assert calls["n"] == 2


def test_ipv4_success_does_not_dial_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    families: list[int] = []
    real = socket.getaddrinfo

    def spy(host: str, port: int, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0):
        families.append(family)
        return real(host, port, family, type, proto, flags)

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    server, port = _serve("127.0.0.1", (200, '{"ok": true}'))
    try:
        client = HttpClient(timeout=2, connect_timeout=2, attempts=1)
        url = f"http://127.0.0.1:{port}/events"
        assert client.get_json(url) == {"ok": True}
        assert client.get_json(url) == {"ok": True}
    finally:
        server.shutdown()
    assert families == [socket.AF_INET, socket.AF_INET]
    assert socket.AF_INET6 not in families


def test_preferred_family_falls_back_when_it_stops_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HttpClient(attempts=1)
    client._preferred_family = socket.AF_INET6
    seen: list[int] = []

    def fake_request(
        self: HttpClient,
        method: str,
        url: str,
        scheme: str,
        host: str,
        port: int,
        path: str,
        data: bytes | None,
        headers: dict[str, str],
        family: int,
    ) -> str:
        seen.append(family)
        if family == socket.AF_INET6:
            raise OSError("2001:db8::5: timed out")
        return '{"ok": true}'

    monkeypatch.setattr(HttpClient, "_request_family", fake_request)
    assert client.get_json("https://gamma-api.polymarket.com/events?slug=nyc") == {"ok": True}
    assert seen == [socket.AF_INET6, socket.AF_INET]
    assert client._preferred_family == socket.AF_INET


def test_ipv4_timeout_falls_back_to_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        server, port = _serve("::1", (200, '{"ok": true}'))
    except OSError:
        pytest.skip("ipv6 localhost is unavailable")
    try:
        def fake_resolve(self: HttpClient, host: str, port_arg: int, family: int):
            if family == socket.AF_INET:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]
            if family == socket.AF_INET6:
                return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]
            raise AssertionError(family)

        monkeypatch.setattr(HttpClient, "_resolve", fake_resolve)
        client = HttpClient(timeout=2, connect_timeout=0.3, attempts=1)
        payload = client.get_json(f"http://gamma-api.polymarket.com:{port}/events?slug=nyc")
        assert payload == {"ok": True}
        assert client._preferred_family == socket.AF_INET6
    finally:
        server.shutdown()


def test_polymarket_uses_public_dns_instead_of_the_system_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    server, port = _serve("127.0.0.1", (200, '{"via": "public"}'))
    calls = {"n": 0}

    def fake_lookup(self: object, host: str):
        calls["n"] += 1
        assert host == "gamma-api.polymarket.com"
        return ("192.0.2.1", "127.0.0.1"), (), 30.0

    def system_dns(*args: object, **kwargs: object):
        raise AssertionError("system resolver was used")

    monkeypatch.setattr("weatherbot.public_dns.PublicDns.lookup", fake_lookup)
    monkeypatch.setattr(socket, "getaddrinfo", system_dns)
    try:
        client = HttpClient(timeout=2, connect_timeout=0.3, attempts=1)
        url = f"http://gamma-api.polymarket.com:{port}/events"
        assert client.get_json(url) == {"via": "public"}
        assert client.get_json(url) == {"via": "public"}
        assert calls["n"] == 1
    finally:
        server.shutdown()
