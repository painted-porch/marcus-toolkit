"""
A minimal MCP-over-HTTP client for run-side tooling.

Lifted from ``dev-tools/experiments/runners/marcus_client.py`` in the Marcus
repository, which the Minecraft plumbing loaded by absolute file path from two
scripts. The transport (``urllib``) lives in :class:`MarcusMCPClient`; the SSE
envelope parsing is pure and unit-testable without a server.

Two additions. :meth:`MarcusMCPClient.call` raises instead of returning
``None``, for callers that must read back every reply body rather than infer
success from silence. And the handshake speaks the current protocol: it offers
version 2025-03-26 (what the Node client sends), keeps the version the server
answers with, sends it as ``MCP-Protocol-Version`` on every later request, and
records why a connect or call failed in ``last_error``. The first CI run of the
live check failed at connect against a server built on a newer ``mcp`` package
than the development machine's, and said nothing about why.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

_DEFAULT_MARCUS_URL = "http://127.0.0.1:4298/mcp/"
#: The protocol version offered on initialize; the server answers with its own.
PROTOCOL_VERSION = "2025-03-26"


class MarcusClientError(RuntimeError):
    """A tool call that produced no readable reply."""


def parse_mcp_envelopes(raw: bytes) -> list[dict[str, Any]]:
    """
    Read every JSON-RPC envelope out of an SSE or plain-JSON response body.

    Parameters
    ----------
    raw : bytes
        The raw HTTP response body.

    Returns
    -------
    List[Dict[str, Any]]
        The envelopes in order; empty when none parse.
    """
    text = raw.decode(errors="replace")
    envelopes: list[dict[str, Any]] = []
    saw_data = False
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        saw_data = True
        try:
            parsed = json.loads(line[len("data:") :].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            envelopes.append(parsed)
    if not saw_data and text.strip().startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return envelopes
        if isinstance(parsed, dict):
            envelopes.append(parsed)
    return envelopes


def parse_mcp_tool_result(raw: bytes) -> dict[str, Any] | None:
    """
    Parse an MCP ``tools/call`` HTTP response into its structured result.

    The tool's own return value sits at ``result.structuredContent.result``.

    Parameters
    ----------
    raw : bytes
        The raw HTTP response body.

    Returns
    -------
    Optional[Dict[str, Any]]
        The tool's structured result, or ``None`` if none is present.
    """
    for envelope in parse_mcp_envelopes(raw):
        result = envelope.get("result")
        if not isinstance(result, dict):
            continue
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            inner = structured.get("result")
            if isinstance(inner, dict):
                return inner
    return None


class MarcusMCPClient:
    """
    Reusable MCP-over-HTTP client for one Marcus server.

    Call :meth:`connect` once, then :meth:`call_tool` or :meth:`call` as often
    as needed; the session id from the handshake is reused.

    Parameters
    ----------
    marcus_url : str
        The Marcus MCP HTTP endpoint. A trailing slash is forced: a POST to
        ``/mcp`` without one gets a 307 that ``urllib`` will not replay.
    timeout : float
        Per-request timeout in seconds.
    """

    def __init__(
        self, marcus_url: str = _DEFAULT_MARCUS_URL, timeout: float = 10.0
    ) -> None:
        self._url = marcus_url.rstrip("/") + "/"
        self._timeout = timeout
        self._session_id: str = ""
        self._protocol_version: str = ""
        self._request_id = 0
        #: Why the last connect or call failed, for a record that must say so.
        self.last_error: str = ""

    @property
    def url(self) -> str:
        """Return the endpoint this client posts to."""
        return self._url

    @property
    def connected(self) -> bool:
        """Return True once :meth:`connect` has established a session."""
        return bool(self._session_id)

    @property
    def protocol_version(self) -> str:
        """Return the protocol version the server answered with, once connected."""
        return self._protocol_version

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _post(self, payload: dict[str, Any]) -> bytes:
        req = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
            body: bytes = resp.read()
            return body

    def connect(self) -> bool:
        """
        Perform the MCP handshake and store the session id and protocol version.

        Returns
        -------
        bool
            True if a session was established; on False, ``last_error`` says why.
        """
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "marcus-toolkit", "version": "0.1"},
            },
        }
        self._session_id = ""
        self._protocol_version = ""
        try:
            req = urllib.request.Request(
                self._url,
                data=json.dumps(init_payload).encode(),
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(
                req, timeout=self._timeout
            ) as resp:  # nosec B310
                session = resp.headers.get("mcp-session-id", "") or ""
                body: bytes = resp.read()
            for envelope in parse_mcp_envelopes(body):
                if envelope.get("error"):
                    self.last_error = f"initialize refused: {envelope['error']}"
                    return False
                result = envelope.get("result")
                if isinstance(result, dict) and result.get("protocolVersion"):
                    self._protocol_version = str(result["protocolVersion"])
            if not session:
                self.last_error = "initialize reply carried no mcp-session-id header"
                return False
            self._session_id = session
            if not self._protocol_version:
                self._protocol_version = PROTOCOL_VERSION
            self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
            self.last_error = ""
            return True
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            self.last_error = f"HTTP {exc.code} from {self._url}: {detail}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        self._session_id = ""
        return False

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """
        Call a tool and return its structured result, or ``None`` on any failure.

        Parameters
        ----------
        name : str
            The tool name.
        arguments : Optional[Dict[str, Any]]
            The tool arguments.

        Returns
        -------
        Optional[Dict[str, Any]]
            The reply body, or ``None`` when not connected, the request failed,
            or the response carried no structured result; ``last_error`` says why.
        """
        if not self._session_id:
            self.last_error = f"{name}: not connected"
            return None
        self._request_id += 1
        try:
            raw = self._post(
                {
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments or {}},
                }
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            self.last_error = f"{name}: HTTP {exc.code}: {detail}"
            return None
        except (urllib.error.URLError, OSError) as exc:
            self.last_error = f"{name}: {type(exc).__name__}: {exc}"
            return None
        result = parse_mcp_tool_result(raw)
        if result is None:
            self.last_error = f"{name}: no structured result in the reply"
        return result

    def call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Call a tool and return its reply body, raising when there is none.

        Parameters
        ----------
        name : str
            The tool name.
        arguments : Optional[Dict[str, Any]]
            The tool arguments.

        Returns
        -------
        Dict[str, Any]
            The reply body.

        Raises
        ------
        MarcusClientError
            When the client is not connected or the call produced no readable
            reply. The message names the tool and the reason.
        """
        if not self._session_id:
            raise MarcusClientError(f"{name}: not connected")
        reply = self.call_tool(name, arguments)
        if reply is None:
            raise MarcusClientError(f"{name}: {self.last_error or 'no readable reply'}")
        return reply
