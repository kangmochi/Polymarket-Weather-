"""JSON HTTP client that dials IPv4 before IPv6.

``urllib`` follows ``getaddrinfo`` order and keeps only the last socket error.
On a host whose IPv6 route is blackholed, that order spends the full timeout
on each AAAA record. Gamma then looks unreachable even when IPv4 would answer.

polymarket.com is resolved through DNS-over-HTTPS. A local resolver that
substitutes an ISP address would otherwise make every market lookup time out.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
from typing import Any
from urllib import error, parse, request

from weatherbot.errors import WeatherbotError
from weatherbot.public_dns import PublicDns, PublicDnsError

USER_AGENT = "weatherbot-paper/0.1"
_FAMILY_ORDER = (socket.AF_INET, socket.AF_INET6)
_CONNECT_ERRORS = (TimeoutError, socket.timeout, OSError, http.client.HTTPException)


class HttpClient:
    """Unauthenticated JSON client. Paper mode sends no credentials."""

    def __init__(
        self,
        timeout: float = 20.0,
        connect_timeout: float = 6.0,
        attempts: int = 2,
    ) -> None:
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.attempts = max(1, attempts)
        self._ssl = ssl.create_default_context()
        self._preferred_family: int | None = None
        self._public_dns = PublicDns(timeout=5.0)
        self._dns_cache: dict[str, tuple[float, object, object]] = {}

    def get_json(self, url: str) -> object:
        return self._read("GET", url, None)

    def post_json(self, url: str, body: object) -> object:
        raw = json.dumps(body).encode("utf-8")
        return self._read("POST", url, raw)

    def _read(self, method: str, url: str, data: bytes | None) -> object:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        last: BaseException | None = None
        payload = ""
        for _attempt in range(self.attempts):
            try:
                payload = self._exchange(method, url, data, headers)
                break
            except WeatherbotError:
                raise
            except _CONNECT_ERRORS as exc:
                last = exc
        else:
            reason = last if last is not None else "request failed"
            raise WeatherbotError("http", f"{method} {url} -> {reason}") from last
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WeatherbotError("http", f"{method} {url} returned non-json") from exc

    def _exchange(self, method: str, url: str, data: bytes | None, headers: dict[str, str]) -> str:
        if _uses_proxy(url):
            return self._exchange_via_proxy(method, url, data, headers)
        parts = parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise WeatherbotError("http", f"{method} {url} -> unsupported url")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        reasons: list[str] = []
        families = self._family_order()
        index = 0
        while index < len(families):
            family = families[index]
            index += 1
            try:
                payload = self._request_family(
                    method,
                    url,
                    parts.scheme,
                    parts.hostname,
                    port,
                    path,
                    data,
                    headers,
                    family,
                )
            except WeatherbotError:
                raise
            except PublicDnsError:
                raise
            except _CONNECT_ERRORS as exc:
                reasons.append(str(exc))
                if self._preferred_family == family:
                    self._preferred_family = None
                    for other in _FAMILY_ORDER:
                        if other not in families:
                            families.append(other)
                continue
            self._preferred_family = family
            return payload
        raise OSError("; ".join(reasons) if reasons else "no route")

    def _family_order(self) -> list[int]:
        if self._preferred_family is not None:
            return [self._preferred_family]
        return list(_FAMILY_ORDER)

    def _request_family(
        self,
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
        infos = self._resolve(host, port, family)
        if not infos:
            raise OSError(f"{host}: no address")
        reasons: list[str] = []
        for info in infos[:2]:
            try:
                return self._request_address(
                    method,
                    url,
                    scheme,
                    host,
                    port,
                    path,
                    data,
                    headers,
                    info,
                )
            except WeatherbotError:
                raise
            except _CONNECT_ERRORS as exc:
                reasons.append(str(exc))
        raise OSError("; ".join(reasons) if reasons else f"{host}: no address")

    def _resolve(self, host: str, port: int, family: int) -> list[tuple[Any, ...]]:
        if _uses_public_dns(host):
            ipv4, ipv6 = self._cached_public(host)
            chosen = ipv4 if family == socket.AF_INET else ipv6
            if not chosen:
                label = "ipv4" if family == socket.AF_INET else "ipv6"
                raise OSError(f"{host}: no {label} from public dns")
            return [_sockaddr(family, ip, port) for ip in chosen]
        try:
            infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise OSError(f"{host}: {exc.strerror or exc}") from exc
        return list(infos)

    def _cached_public(self, host: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        now = time.monotonic()
        cached = self._dns_cache.get(host)
        if cached is not None and cached[0] > now:
            if isinstance(cached[1], PublicDnsError):
                raise cached[1]
            return cached[1], cached[2]  # type: ignore[return-value]
        try:
            ipv4, ipv6, ttl = self._public_dns.lookup(host)
        except PublicDnsError as exc:
            self._dns_cache[host] = (now + 30.0, exc, ())
            raise
        self._dns_cache[host] = (now + ttl, ipv4, ipv6)
        return ipv4, ipv6

    def _request_address(
        self,
        method: str,
        url: str,
        scheme: str,
        host: str,
        port: int,
        path: str,
        data: bytes | None,
        headers: dict[str, str],
        info: tuple[Any, ...],
    ) -> str:
        sockaddr = info[4]
        address = str(sockaddr[0])
        raw = socket.socket(info[0], socket.SOCK_STREAM)
        raw.settimeout(self.connect_timeout)
        try:
            raw.connect(sockaddr)
        except Exception as exc:
            raw.close()
            raise OSError(f"{address}: {exc}") from exc
        raw.settimeout(self.timeout)
        if scheme == "https":
            try:
                raw = self._ssl.wrap_socket(raw, server_hostname=host)
            except Exception as exc:
                raw.close()
                raise OSError(f"{address}: {exc}") from exc
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host,
                port,
                timeout=self.timeout,
                context=self._ssl,
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
        conn.sock = raw
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            body = response.read()
            status = response.status
        finally:
            conn.close()
        if status >= 400:
            detail = body.decode("utf-8", errors="replace")[:500]
            raise WeatherbotError("http", f"{method} {url} -> {status} {detail}")
        return body.decode("utf-8")

    def _exchange_via_proxy(
        self,
        method: str,
        url: str,
        data: bytes | None,
        headers: dict[str, str],
    ) -> str:
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise WeatherbotError("http", f"{method} {url} -> {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise OSError(str(exc.reason)) from exc


def _uses_public_dns(host: str) -> bool:
    name = host.rstrip(".").lower()
    if not name or ":" in name or name.replace(".", "").isdigit():
        return False
    return name == "polymarket.com" or name.endswith(".polymarket.com")


def _sockaddr(family: int, ip: str, port: int) -> tuple[Any, ...]:
    if family == socket.AF_INET:
        return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
    return (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0))


def _uses_proxy(url: str) -> bool:
    parts = parse.urlsplit(url)
    host = parts.hostname
    if not host or request.proxy_bypass(host):
        return False
    return parts.scheme in request.getproxies()
