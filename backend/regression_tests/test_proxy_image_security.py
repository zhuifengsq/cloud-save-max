import socket

import pytest
from starlette.requests import Request

from app.api.routes import media_discovery
from app.services import proxy_image_cache as cache


@pytest.mark.parametrize("url", [
    "https://example-douban.com/image",
    "https://user@img1.doubanio.com/image",
    "https://img1.doubanio.com:8080/image",
    "https://[::1]/image",
])
def test_invalid_url_never_reaches_cache_or_network(monkeypatch, url):
    def unexpected(**kwargs):
        pytest.fail("invalid URL reached the image cache")

    monkeypatch.setattr(media_discovery, "try_build_cached_response", unexpected)
    request = Request({"type": "http", "headers": []})
    assert media_discovery.get_proxy_image(request, url).status_code == 400


class Upstream:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body
        self.closed = False
        self.released = False

    def read1(self, size, **kwargs):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


@pytest.fixture
def upstream(monkeypatch):
    def configure(response, addresses=("8.8.8.8",)):
        calls = []
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", (address, 443, 0, 0))
            if ":" in address else (socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))
            for address in addresses
        ])

        def connect(address, port, *, scheme, pool_kwargs):
            calls.append((address, port, pool_kwargs))
            class Pool:
                def urlopen(self, method, path, **options):
                    calls.append((method, path, options))
                    return response
            return Pool()

        monkeypatch.setattr(cache._image_pools, "connection_from_host", connect)
        return calls
    return configure


@pytest.mark.parametrize(("status", "headers", "body", "expected"), [
    (302, {"Location": "http://127.0.0.1"}, b"", 502),
    (200, {"Content-Type": "text/html"}, b"bad", 502),
    (200, {"Content-Type": "image/png", "Content-Length": "5"}, b"12345", 413),
    (200, {"Content-Type": "image/png"}, b"12345", 413),
    (200, {"Content-Type": "image/png"}, b"1234", 200),
])
def test_upstream_limits_in_bypass_mode(monkeypatch, upstream, status, headers, body, expected):
    response = Upstream(status, headers, body)
    calls = upstream(response)
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_enabled", False)
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_max_file_bytes", 4)
    request = Request({"type": "http", "headers": []})
    result = media_discovery.get_proxy_image(request, "https://img1.doubanio.com/p?q=1")
    assert result.status_code == expected
    assert len(calls) == 2
    assert calls[0][0] == "8.8.8.8"
    assert calls[0][2]["server_hostname"] == "img1.doubanio.com"
    assert calls[1][2]["redirect"] is False
    assert response.closed and response.released


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "::1", "::ffff:127.0.0.1", "fec0::1", "64:ff9b::a00:1"])
def test_nonpublic_dns_never_connects(monkeypatch, upstream, address):
    calls = upstream(None, addresses=("8.8.8.8", address))
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_enabled", False)
    request = Request({"type": "http", "headers": []})
    assert media_discovery.get_proxy_image(request, "https://img1.doubanio.com/p").status_code == 400
    assert not calls


def test_dns_changes_do_not_send_a_request_to_a_new_private_address(monkeypatch, upstream):
    calls = upstream(Upstream(200, {"Content-Type": "image/jpeg"}, b"jpeg"))
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_enabled", False)
    dns_answers = iter(["8.8.8.8", "127.0.0.1"])

    def resolve(host, port, **kwargs):
        assert host == "img1.doubanio.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (next(dns_answers), port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    request = Request({"type": "http", "headers": []})
    url = "https://img1.doubanio.com/image"
    assert media_discovery.get_proxy_image(request, url).status_code == 200
    assert media_discovery.get_proxy_image(request, url).status_code == 400
    assert len(calls) == 2 and calls[0][0] == "8.8.8.8"


def test_img9_public_poster_uses_working_douban_image_host(monkeypatch, upstream):
    response = Upstream(200, {"Content-Type": "image/jpeg"}, b"jpeg")
    calls = upstream(response)
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_enabled", False)
    request = Request({"type": "http", "headers": []})
    path = "/view/photo/s_ratio_poster/public/p2936279046.jpg"
    result = media_discovery.get_proxy_image(request, "https://img9.doubanio.com" + path)
    assert result.status_code == 200
    assert calls[0][2]["server_hostname"] == "img2.doubanio.com"
    assert calls[1][1] == path
    assert calls[1][2]["headers"]["Host"] == "img2.doubanio.com"


def test_cache_miss_then_hit_and_304_without_second_dns_lookup(tmp_path, monkeypatch, upstream):
    response = Upstream(200, {"Content-Type": "image/png"}, b"png")
    calls = upstream(response, addresses=("2606:4700:4700::1111",))
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_enabled", True)
    monkeypatch.setattr(media_discovery.settings, "media_proxy_image_cache_dir", str(tmp_path))
    request = Request({"type": "http", "headers": []})
    url = "https://img1.doubanio.com/p"
    miss = media_discovery.get_proxy_image(request, url)
    assert miss.headers["X-Proxy-Cache"] == "MISS"
    assert miss.body == b"png"
    assert response.closed
    assert calls[0][0] == "2606:4700:4700::1111"

    def unexpected_dns(*args, **kwargs):
        pytest.fail("cache hit must not resolve the upstream")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_dns)
    hit = media_discovery.get_proxy_image(request, url)
    assert hit.headers["X-Proxy-Cache"] == "HIT"
    conditional = Request({"type": "http", "headers": [(b"if-none-match", miss.headers["etag"].encode())]})
    not_modified = media_discovery.get_proxy_image(conditional, url)
    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == miss.headers["etag"]
