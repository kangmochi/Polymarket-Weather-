import pytest

from weatherbot.public_dns import PublicDns, PublicDnsError


def test_lookup_reads_addresses_and_follows_a_cname() -> None:
    def fetch(ip: str, server_name: str, path: str) -> object:
        assert ip == "1.1.1.1"
        kind = "AAAA" if "type=AAAA" in path else "A"
        if "name=gamma-api.polymarket.com" in path and kind == "A":
            return {
                "Status": 0,
                "Answer": [
                    {"type": 5, "TTL": 20, "data": "gamma.example.net."},
                ],
            }
        if "name=gamma.example.net" in path and kind == "A":
            return {
                "Status": 0,
                "Answer": [
                    {"type": 1, "TTL": 15, "data": "104.18.34.205"},
                    {"type": 1, "TTL": 40, "data": "not-an-ip"},
                ],
            }
        if kind == "AAAA":
            return {"Status": 0, "Answer": [{"type": 28, "TTL": 15, "data": "2606:4700:4408::ac40:9933"}]}
        raise AssertionError(path)

    ipv4, ipv6, ttl = PublicDns(fetch=fetch).lookup("gamma-api.polymarket.com")
    assert ipv4 == ("104.18.34.205",)
    assert ipv6 == ("2606:4700:4408::ac40:9933",)
    assert ttl == 60.0


def test_lookup_tries_the_next_resolver_when_the_first_fails() -> None:
    seen: list[str] = []

    def fetch(ip: str, server_name: str, path: str) -> object:
        seen.append(ip)
        if ip == "1.1.1.1":
            raise TimeoutError("timed out")
        if path.endswith("type=AAAA"):
            return {"Status": 0, "Answer": []}
        return {"Status": 0, "Answer": [{"type": 1, "TTL": 120, "data": "172.64.153.51"}]}

    ipv4, ipv6, ttl = PublicDns(fetch=fetch).lookup("clob.polymarket.com")
    assert ipv4 == ("172.64.153.51",)
    assert ipv6 == ()
    assert ttl == 120.0
    assert seen[0] == "1.1.1.1"
    assert "8.8.8.8" in seen


def test_lookup_fails_when_every_resolver_fails() -> None:
    def fetch(ip: str, server_name: str, path: str) -> object:
        raise OSError("refused")

    with pytest.raises(PublicDnsError, match="public dns failed"):
        PublicDns(fetch=fetch).lookup("gamma-api.polymarket.com")
