"""The guard for user-supplied URLs (no SSRF): a fake resolver and a fake network."""

import asyncio
from typing import Any

import httpx2
import pytest

from llm_steward.errors import TooLarge
from llm_steward.outbound import UrlNotAllowed, check, client, request

PUBLIC = "93.184.216.34"
DNS = {
    "hooks.example.com": [PUBLIC],
    "docs.example.com": [PUBLIC],
    "evil-example.com": [PUBLIC],
    "split.example.com": [PUBLIC, "10.0.0.7"],  # one answer internal: refused
    "rebind.example.com": ["127.0.0.1"],
    "v6.example.com": ["2606:4700::1111"],
}


async def resolve(host: str, port: int) -> list[str]:
    if host in DNS:
        return DNS[host]
    try:  # an IP literal resolves to itself
        import ipaddress

        return [str(ipaddress.ip_address(host))]
    except ValueError:
        raise OSError("no such host") from None


def _check(url: str, **kw: Any) -> Any:
    return asyncio.run(check(url, resolve=resolve, **kw))


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "[::1]",
        "[fd00::1]",
        "[fe80::1]",
        "[::ffff:127.0.0.1]",  # IPv4 written as IPv6
    ],
)
def test_internal_addresses_are_refused(address: str) -> None:
    with pytest.raises(UrlNotAllowed):
        _check(f"https://{address}/hook")


def test_a_name_with_any_internal_answer_is_refused() -> None:
    with pytest.raises(UrlNotAllowed):
        _check("https://split.example.com/x")
    with pytest.raises(UrlNotAllowed):
        _check("https://rebind.example.com/x")
    with pytest.raises(UrlNotAllowed):
        _check("https://nowhere.example.com/x")  # does not resolve


def test_https_only_and_no_credentials() -> None:
    with pytest.raises(UrlNotAllowed):
        _check("http://hooks.example.com/x")
    with pytest.raises(UrlNotAllowed):
        _check("ftp://hooks.example.com/x")
    with pytest.raises(UrlNotAllowed):
        _check("https://user:pass@hooks.example.com/x")


def test_the_connection_goes_to_the_checked_address() -> None:
    target = _check("https://hooks.example.com/in?a=1")
    assert target.url == f"https://{PUBLIC}/in?a=1"
    assert (target.host, target.host_header) == ("hooks.example.com", "hooks.example.com")
    v6 = _check("https://v6.example.com:8443/x")
    assert v6.url == "https://[2606:4700::1111]:8443/x"
    assert v6.host_header == "v6.example.com:8443"


def test_local_development_may_reach_private_addresses_over_http() -> None:
    target = _check("http://127.0.0.1:9000/hook", allow_private=True)
    assert target.url == "http://127.0.0.1:9000/hook"


def test_an_allowlist_admits_its_domains_and_their_subdomains_only() -> None:
    allow = ("example.com",)
    assert _check("https://docs.example.com/a.pdf", allowlist=allow).host == "docs.example.com"
    with pytest.raises(UrlNotAllowed):
        _check("https://evil-example.com/a.pdf", allowlist=allow)
    with pytest.raises(UrlNotAllowed):
        _check(f"https://{PUBLIC}/a.pdf", allowlist=allow)


# --- requests through the guard --------------------------------------------------------------


def _send(routes: dict[str, httpx2.Response], url: str, **kw: Any) -> Any:
    seen: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return routes[req.headers["host"] + req.url.path]

    async def run() -> Any:
        async with client(httpx2.MockTransport(handler)) as http:
            return await request(http, "GET", url, max_bytes=100, resolve=resolve, **kw)

    return asyncio.run(run()), seen


def test_a_request_is_pinned_with_its_hostname_kept() -> None:
    fetched, [sent] = _send(
        {"docs.example.com/a.md": httpx2.Response(200, content=b"# hi")},
        "https://docs.example.com/a.md",
    )
    assert fetched.body == b"# hi" and fetched.status_code == 200
    assert sent.url.host == PUBLIC
    assert sent.headers["host"] == "docs.example.com"
    assert sent.extensions["sni_hostname"] == "docs.example.com"


def test_redirects_are_checked_like_the_first_url() -> None:
    routes = {
        "docs.example.com/a": httpx2.Response(
            302, headers={"location": "https://docs.example.com/b"}
        ),
        "docs.example.com/b": httpx2.Response(200, content=b"ok"),
        "docs.example.com/inside": httpx2.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        ),
        "docs.example.com/loop": httpx2.Response(302, headers={"location": "/loop"}),
    }
    fetched, _ = _send(routes, "https://docs.example.com/a", follow_redirects=True)
    assert (fetched.body, fetched.url) == (b"ok", "https://docs.example.com/b")
    with pytest.raises(UrlNotAllowed):
        _send(routes, "https://docs.example.com/inside", follow_redirects=True)
    with pytest.raises(UrlNotAllowed):
        _send(routes, "https://docs.example.com/loop", follow_redirects=True)
    unfollowed, _ = _send(routes, "https://docs.example.com/a")  # webhooks: a 3xx is the answer
    assert unfollowed.status_code == 302


def test_a_body_over_the_limit_is_refused() -> None:
    routes = {"docs.example.com/big": httpx2.Response(200, content=b"x" * 101)}
    with pytest.raises(TooLarge):
        _send(routes, "https://docs.example.com/big")
