"""Unit tests for marcus_toolkit.client."""

import io
import json

import pytest

from marcus_toolkit.client import (
    MarcusClientError,
    MarcusMCPClient,
    parse_mcp_tool_result,
)

pytestmark = pytest.mark.unit

SSE = (
    b"event: message\n"
    b'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"x"}],'
    b'"structuredContent":{"result":{"success":true,"project_id":"p1"}}}}\n\n'
)


def test_parse_returns_the_structured_result():
    """The tool's own return value sits at result.structuredContent.result."""
    assert parse_mcp_tool_result(SSE) == {"success": True, "project_id": "p1"}


def test_parse_returns_none_without_a_structured_result():
    """A body with no data line, or a non-dict result, parses to None."""
    assert parse_mcp_tool_result(b"nothing here") is None
    assert (
        parse_mcp_tool_result(
            b'data: {"jsonrpc":"2.0","result":{"structuredContent":{"result":[1]}}}\n'
        )
        is None
    )


def test_url_gets_a_trailing_slash():
    """A POST to /mcp without the slash is redirected and lost; the client forces it."""
    assert (
        MarcusMCPClient("http://127.0.0.1:4777/mcp").url == "http://127.0.0.1:4777/mcp/"
    )


def test_call_tool_is_none_and_call_raises_when_not_connected():
    """Without a session nothing is sent."""
    client = MarcusMCPClient("http://127.0.0.1:1/mcp")
    assert client.call_tool("get_project_status") is None
    with pytest.raises(MarcusClientError, match="not connected"):
        client.call("get_project_status")


def test_call_returns_the_reply_body_and_raises_on_an_unreadable_one(monkeypatch):
    """call() reads the body back; an empty reply is an error naming the tool."""
    client = MarcusMCPClient("http://127.0.0.1:1/mcp")
    client._session_id = "s"
    monkeypatch.setattr(client, "_post", lambda payload: SSE)
    assert client.call("create_project", {"x": 1})["project_id"] == "p1"
    monkeypatch.setattr(client, "_post", lambda payload: b"")
    with pytest.raises(MarcusClientError, match="create_project"):
        client.call("create_project")


class _Resp:
    """A fake urlopen response: headers, a body, a context manager."""

    def __init__(self, body, session="s1"):
        self._body = body
        self.headers = {"mcp-session-id": session} if session else {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


INIT_OK = (
    b'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18",'
    b'"capabilities":{},"serverInfo":{"name":"marcus"}}}\n'
)
INIT_REFUSED = (
    b'data: {"jsonrpc":"2.0","id":1,"error":{"code":-32602,'
    b'"message":"Unsupported protocol version"}}\n'
)


def test_connect_stores_the_session_and_the_servers_protocol_version(monkeypatch):
    """The server's protocolVersion goes back as a header on every later request."""
    import marcus_toolkit.client as mod

    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(req)
        return _Resp(INIT_OK if b"initialize" in req.data else b"")

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    client = MarcusMCPClient("http://127.0.0.1:1/mcp")
    assert client.connect() is True
    assert client.connected
    assert client.protocol_version == "2025-06-18"
    assert sent[1].get_header("Mcp-protocol-version") == "2025-06-18"
    assert sent[1].get_header("Mcp-session-id") == "s1"
    assert json.loads(sent[0].data)["params"]["protocolVersion"] == "2025-03-26"


def test_connect_reports_why_it_failed(monkeypatch):
    """A refused initialize, a missing session header and an HTTP error each say so."""
    import marcus_toolkit.client as mod

    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda req, timeout=None: _Resp(INIT_REFUSED)
    )
    client = MarcusMCPClient("http://127.0.0.1:1/mcp")
    assert client.connect() is False
    assert "Unsupported protocol version" in client.last_error

    monkeypatch.setattr(
        mod.urllib.request, "urlopen", lambda req, timeout=None: _Resp(INIT_OK, "")
    )
    assert client.connect() is False
    assert "mcp-session-id" in client.last_error

    def boom(req, timeout=None):
        raise mod.urllib.error.HTTPError(
            req.full_url, 406, "Not Acceptable", {}, io.BytesIO(b"needs event-stream")
        )

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    assert client.connect() is False
    assert "HTTP 406" in client.last_error
    assert "event-stream" in client.last_error
