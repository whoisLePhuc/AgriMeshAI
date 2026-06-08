"""MCP (Model Context Protocol) client for edge_agent.

Connects to MCP servers over the **stdio transport** using JSON-RPC 2.0.
Only standard-library modules are used (``subprocess``, ``json``,
``threading``).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any

from edge_agent.logger import get_logger
from edge_agent.tool import Tool

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "tinyagent", "version": "0.1.0"}


class MCPServer:
    """A connection to an MCP server over the stdio transport.

    The server is launched as a subprocess.  After :meth:`connect` the
    discovered tools are available via the :attr:`tools` property as
    regular :class:`~edge_agent.tool.Tool` objects that proxy calls back
    to the server via JSON-RPC.

    Supports the context-manager protocol::

        with MCPServer("fs", command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]) as server:
            print(server.tools)
    """

    def __init__(
        self,
        name: str,
        *,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._command = list(command)
        self._env = env
        self._process: subprocess.Popen[bytes] | None = None
        self._tools: list[Tool] = []
        self._request_id = 0
        self._connected = False
        self._logger = get_logger(f"mcp.{name}")

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"MCPServer({self.name!r}, tools={len(self._tools)}, {status})"

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> MCPServer:
        if not self._connected:
            self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # -- public API -----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[Tool]:
        """Tools discovered from the MCP server (empty until connected)."""
        return list(self._tools)

    def connect(self) -> None:
        """Launch the server process, perform the MCP handshake, and
        discover tools."""
        if self._connected:
            return

        proc_env = {**os.environ, **(self._env or {})}

        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
        )

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
        )
        self._stderr_thread.start()

        self._initialize()
        self._discover_tools()
        self._connected = True
        self._logger.info(
            "connected — %d tool(s) discovered", len(self._tools),
        )

    def close(self) -> None:
        """Terminate the server process and release resources."""
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            self._process.kill()
            self._process.wait()
        self._process = None
        self._connected = False
        self._tools.clear()
        self._logger.info("closed")

    # -- MCP protocol ---------------------------------------------------------

    def _initialize(self) -> None:
        result = self._send_request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        server_info = result.get("serverInfo", {})
        self._logger.info(
            "server %s (protocol %s)",
            server_info.get("name", "unknown"),
            result.get("protocolVersion", "unknown"),
        )
        self._send_notification("notifications/initialized", {})

    def _discover_tools(self) -> None:
        result = self._send_request("tools/list", {})
        self._tools.clear()
        for tool_def in result.get("tools", []):
            self._tools.append(self._make_tool(tool_def))

    def _make_tool(self, tool_def: dict[str, Any]) -> Tool:
        tool_name: str = tool_def["name"]
        description: str = tool_def.get("description", "")
        input_schema: dict[str, Any] = tool_def.get(
            "inputSchema", {"type": "object", "properties": {}},
        )

        server_ref = self

        def _call_mcp(**kwargs: Any) -> str:
            return server_ref._call_tool(tool_name, kwargs)

        return Tool(
            fn=_call_mcp,
            name=tool_name,
            description=description,
            parameters=input_schema,
        )

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Send a ``tools/call`` request and return the text content."""
        result = self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        is_error = result.get("isError", False)
        content_parts = result.get("content", [])

        texts: list[str] = []
        for part in content_parts:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))

        text = "\n".join(texts) if texts else str(result)

        if is_error:
            self._logger.warning("tool %r returned error: %s", name, text)

        return text

    # -- JSON-RPC 2.0 transport -----------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request(
        self, method: str, params: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and block until the matching response."""
        req_id = self._next_id()
        self._write_message({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        })

        while True:
            msg = self._read_message()
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(
                        f"MCP error ({err.get('code')}): {err.get('message')}"
                    )
                return msg.get("result", {})

    def _send_notification(
        self, method: str, params: dict[str, Any],
    ) -> None:
        """Send a JSON-RPC notification (no ``id``, no response expected)."""
        self._write_message({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })

    def _write_message(self, msg: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        line = json.dumps(msg, separators=(",", ":")) + "\n"
        try:
            self._process.stdin.write(line.encode())
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ConnectionError(
                f"MCP server {self.name!r} is not responding"
            ) from exc

    def _read_message(self) -> dict[str, Any]:
        assert self._process is not None and self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            raise ConnectionError(
                f"MCP server {self.name!r} closed the connection"
            )
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConnectionError(
                f"MCP server {self.name!r} sent invalid JSON: {line!r}"
            ) from exc

    # -- helpers --------------------------------------------------------------

    def _drain_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for raw_line in self._process.stderr:
            self._logger.debug(
                "server stderr: %s", raw_line.decode(errors="replace").rstrip(),
            )


# ── config loader ───────────────────────────────────────────────────────────


def load_mcp_config(
    path: str | os.PathLike[str],
    servers: list[str] | None = None,
) -> dict[str, MCPServer]:
    """Load MCP server definitions from a JSON config file.

    The file uses the Claude Desktop format::

        {"mcpServers": {"name": {"command": "...", "args": [...], "env": {...}}}}

    Returns a dict of **unconnected** :class:`MCPServer` instances keyed by
    server name.  Pass *servers* to load only a subset by name.
    """
    from pathlib import Path

    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    all_servers = data["mcpServers"]

    if servers is not None:
        available = set(all_servers)
        for name in servers:
            if name not in available:
                raise ValueError(
                    f"Server {name!r} not found in config. "
                    f"Available: {sorted(available)}"
                )
        entries = {name: all_servers[name] for name in servers}
    else:
        entries = all_servers

    result: dict[str, MCPServer] = {}
    for name, defn in entries.items():
        command_str = defn["command"]
        args = defn.get("args", [])
        env = defn.get("env") or None
        result[name] = MCPServer(name, command=[command_str, *args], env=env)

    return result