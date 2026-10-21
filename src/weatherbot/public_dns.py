"""Resolve names through DNS-over-HTTPS so a poisoned local resolver is ignored.

Some networks answer for polymarket.com with an address inside the ISP, then
the TCP connection times out. Asking 1.1.1.1 or 8.8.8.8 by IP, and checking
the TLS certificate, returns the addresses the name actually publishes.
"""

from __future__ import annotations

import json
import socket
import ssl
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import http.client

_A = 1
_CNAME = 5
_AAAA = 28
_SERVERS = (
    ("1.1.1.1", "cloudflare-dns.com", "/dns-query"),
    ("8.8.8.8", "dns.google", "/resolve"),
    ("1.0.0.1", "cloudflare-dns.com", "/dns-query"),
    ("8.8.4.4", "dns.google", "/resolve"),
)
Fetch = Callable[[str, str, str], object]


class PublicDnsError(OSError):
    """Every public resolver failed, or the name has no address."""


class PublicDns:
    def __init__(self, timeout: float = 5.0, fetch: Fetch | None = None) -> None:
        self.timeout = timeout
        self._fetch = fetch or self._https_json
        self._ssl = ssl.create_default_context()

    def lookup(self, host: str) -> tuple[tuple[str, ...], tuple[str, ...], float]:
        ipv4, ttl4 = self._collect(host, _A)
        ipv6, ttl6 = self._collect(host, _AAAA)
        if not ipv4 and not ipv6:
            raise PublicDnsError(f"{host}: public dns returned no addresses")
        ttls = [item for item in (ttl4, ttl6) if item is not None]
        ttl = min(ttls) if ttls else 120.0
        return tuple(ipv4), tuple(ipv6), float(min(600.0, max(60.0, ttl)))

    def _collect(self, name: str, record_type: int, depth: int = 0) -> tuple[list[str], float | None]:
        payload = self._query(name, record_type)
        found: list[str] = []
        ttl: float | None = None
        cname: str | None = None
        for item in _answers(payload):
            item_type = item.get("type")
            data = item.get("data")
            if not isinstance(data, str):
                continue
            if item_type == record_type:
                address = _address(data, record_type)
                if address is None:
                    continue
                found.append(address)
                ttl = _earlier(ttl, item.get("TTL"))
            elif item_type == _CNAME and cname is None:
                cname = data.strip().rstrip(".")
        if found or cname is None or depth >= 4:
            return found, ttl
        return self._collect(cname, record_type, depth + 1)

    def _query(self, name: str, record_type: int) -> object:
        label = "A" if record_type == _A else "AAAA"
        path_query = f"?name={quote(name)}&type={label}"
        last: Exception | None = None
        for ip, server_name, prefix in _SERVERS:
            try:
                payload = self._fetch(ip, server_name, prefix + path_query)
            except (TimeoutError, socket.timeout, OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
                last = exc
                continue
            if isinstance(payload, dict) and payload.get("Status") not in (0, None):
                last = PublicDnsError(f"status {payload.get('Status')}")
                continue
            return payload
        detail = last if last is not None else "no resolver"
        raise PublicDnsError(f"{name}: public dns failed: {detail}")

    def _https_json(self, ip: str, server_name: str, path: str) -> object:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(self.timeout)
        try:
            raw.connect((ip, 443))
        except Exception:
            raw.close()
            raise
        raw.settimeout(self.timeout)
        try:
            tls = self._ssl.wrap_socket(raw, server_hostname=server_name)
        except Exception:
            raw.close()
            raise
        conn = http.client.HTTPSConnection(server_name, 443, timeout=self.timeout, context=self._ssl)
        conn.sock = tls
        try:
            conn.request(
                "GET",
                path,
                headers={
                    "Accept": "application/dns-json",
                    "Host": server_name,
                    "User-Agent": "weatherbot-paper/0.1",
                },
            )
            response = conn.getresponse()
            body = response.read()
            status = response.status
        finally:
            conn.close()
        if status >= 400:
            detail = body.decode("utf-8", errors="replace")[:200]
            raise OSError(f"{server_name}: {status} {detail}")
        return json.loads(body.decode("utf-8"))


def _answers(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("Answer")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _address(data: str, record_type: int) -> str | None:
    text = data.strip().rstrip(".")
    family = socket.AF_INET if record_type == _A else socket.AF_INET6
    try:
        socket.inet_pton(family, text)
    except OSError:
        return None
    return text


def _earlier(current: float | None, ttl: object) -> float | None:
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
        return current
    value = float(ttl)
    if current is None:
        return value
    return min(current, value)
