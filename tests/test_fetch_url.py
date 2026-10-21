from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.fetch_url import is_public_address, register_fetch_url_tool, strip_html


class FakeResponse:
    def __init__(self, content: bytes, content_type: str = "text/html", status_ok: bool = True) -> None:
        self.content = content
        self.headers = {"Content-Type": content_type}
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("HTTP error")


class FakeHttp:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def get(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self._response


def test_strip_html_removes_tags_and_scripts() -> None:
    html = "<html><head><style>.x{}</style></head><body><p>Hello <b>world</b></p><script>evil()</script></body></html>"
    assert strip_html(html) == "Hello world"


def test_is_public_address_rejects_private_ranges() -> None:
    assert is_public_address("127.0.0.1") is False
    assert is_public_address("10.0.0.5") is False
    assert is_public_address("192.168.1.1") is False
    assert is_public_address("169.254.1.1") is False


def test_is_public_address_accepts_public_ip() -> None:
    assert is_public_address("8.8.8.8") is True


def test_fetch_url_rejects_non_http_scheme() -> None:
    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=FakeHttp(FakeResponse(b"")), resolve_host=lambda h: "8.8.8.8")

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "file:///etc/passwd"})))

    assert "error" in result


def test_fetch_url_refuses_private_address() -> None:
    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=FakeHttp(FakeResponse(b"")), resolve_host=lambda h: "127.0.0.1")

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "http://localhost/secret"})))

    assert "error" in result


def test_fetch_url_returns_stripped_text_for_html(tmp_path=None) -> None:
    http = FakeHttp(FakeResponse(b"<html><body><h1>Title</h1><p>Body text</p></body></html>"))
    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=http, resolve_host=lambda h: "93.184.216.34")

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "https://example.com"})))

    assert result == {"url": "https://example.com", "content": "Title Body text"}


def test_fetch_url_does_not_strip_non_html_content() -> None:
    http = FakeHttp(FakeResponse(b'{"a": 1}', content_type="application/json"))
    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=http, resolve_host=lambda h: "93.184.216.34")

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "https://example.com/api"})))

    assert result["content"] == '{"a": 1}'


def test_fetch_url_handles_dns_resolution_failure() -> None:
    def failing_resolve(host):
        raise OSError("no such host")

    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=FakeHttp(FakeResponse(b"")), resolve_host=failing_resolve)

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "https://nonexistent.invalid"})))

    assert "error" in result


def test_fetch_url_handles_http_failure_without_raising() -> None:
    http = FakeHttp(FakeResponse(b"", status_ok=False))
    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=http, resolve_host=lambda h: "93.184.216.34")

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "https://example.com"})))

    assert "error" in result


def test_fetch_url_truncates_long_content() -> None:
    long_text = "a" * 30000
    http = FakeHttp(FakeResponse(long_text.encode(), content_type="text/plain"))
    registry = ToolRegistry()
    register_fetch_url_tool(registry, http=http, resolve_host=lambda h: "93.184.216.34")

    result = json.loads(registry.execute("fetch_url", json.dumps({"url": "https://example.com/big"})))

    assert len(result["content"]) == 20000
