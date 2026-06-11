"""MCP server — the unified endpoint that wires everything together.

Connects discovery, device_manager, fleet tools, and storage behind a single
MCP server. Clients see one flat tool catalog: namespaced device tools
plus fleet-level tools.

Supports two modes:
- **stdio**: Ephemeral — one MCP client owns the process (``AgriMeshAI start``)
- **daemon**: Long-running — background recording + Streamable HTTP endpoint (``AgriMeshAI daemon``)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server import InitializationOptions
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ServerCapabilities,
    TextContent,
    Tool,
    ToolsCapability,
)

from mcp_server.gateway.recorder import run_recorder
from recorder.retention import run_cleanup
from system.manager import SystemManager

logger = logging.getLogger(__name__)


class AgriMeshAIServer:
    """Chỉ xử lý MCP protocol. Nhận SystemManager qua DI."""

    def __init__(self, system: SystemManager) -> None:
        self._system = system
        self._server = Server(name="agrimesh", version="0.1.0")
        self._daemon_active = False
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self._server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_tools() -> list[Tool]:
            return self.handle_list_tools()

        @self._server.call_tool()  # type: ignore[untyped-decorator]
        async def call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> CallToolResult:
            return await self.handle_call_tool(name, arguments)

    def handle_list_tools(self) -> list[Tool]:
        """Build the unified tool catalog from device + fleet tools."""
        return self._system.list_tools()

    async def handle_call_tool(
        self, name: str, arguments: dict[str, Any] | None
    ) -> CallToolResult:
        """Route a tool call to the correct handler."""
        args = arguments or {}

        # Fleet tools
        if name.startswith("fleet."):
            try:
                result = await self._system.call_tool(name, args)
                if result.success:
                    data = result.data
                    return CallToolResult(
                        content=[TextContent(type="text", text=str(data))],
                        structuredContent=data,
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=result.error or "unknown error")],
                        isError=True,
                    )
            except Exception as e:
                logger.exception("fleet tool %s failed", name)
                return CallToolResult(
                    content=[TextContent(type="text", text=str(e))],
                    isError=True,
                )

        # Device tools
        adapter_result = await self._system.call_tool(name, args)
        if adapter_result.success:
            # Only record on client tool calls — the background
            # recorder handles periodic recording in daemon mode.
            if not self._daemon_active:
                await self._maybe_record(name, adapter_result.data)
            data = {"data": adapter_result.data}
            return CallToolResult(
                content=[TextContent(type="text", text=str(data))],
                structuredContent=data,
            )
        else:
            return CallToolResult(
                content=[TextContent(
                    type="text", text=adapter_result.error or "unknown error",
                )],
                isError=True,
            )

    # Types that can be stored as float in the time-series store
    _NUMERIC_TYPES = {"float", "number", "int", "integer"}

    async def _maybe_record(self, tool_name: str, raw_data: Any) -> None:
        """Record a sensor reading to the store if the tool returns a numeric type.

        Best-effort — failures are logged, never raised. The tool call
        has already succeeded; storage is a side effect.
        """
        dm = self._system.device_manager
        store = self._system.store
        if store is None:
            return

        route = dm.get_route(tool_name)
        if not route or not route.returns:
            return

        if route.returns.type not in self._NUMERIC_TYPES:
            return

        try:
            value = float(raw_data)
        except (TypeError, ValueError):
            logger.debug(
                "skipping store for %s: cannot convert %r to float",
                tool_name, raw_data,
            )
            return

        unit = route.returns.unit or ""
        try:
            await store.record(
                device_id=route.device.name,
                sensor_id=route.tool_name,
                value=value,
                unit=unit,
            )
            await self._system.event_queue.publish(
                "reading_recorded",
                device_id=route.device.name,
                sensor_id=route.tool_name,
                value=value,
                unit=unit,
            )
        except Exception:
            logger.warning("failed to record reading for %s", tool_name, exc_info=True)

    # ------------------------------------------------------------------
    # stdio transport
    # ------------------------------------------------------------------

    async def serve_stdio(self) -> None:
        """Serve MCP over stdio transport. Call system.start() first."""
        async with stdio_server() as (read_stream, write_stream):
            init_options = InitializationOptions(
                server_name="agrimesh",
                server_version="0.1.0",
                capabilities=ServerCapabilities(tools=ToolsCapability()),
            )
            await self._server.run(
                read_stream, write_stream, init_options
            )

    async def run_stdio(self) -> None:
        """Start the system and run over stdio transport."""
        try:
            await self._system.start()
            await self.serve_stdio()
        finally:
            await self._system.stop()

    # ------------------------------------------------------------------
    # HTTP transport
    # ------------------------------------------------------------------

    def _build_http_app(self) -> tuple[Any, Any]:
        """Build a Starlette ASGI app that serves MCP over Streamable HTTP.

        Imports starlette and the MCP StreamableHTTPSessionManager lazily
        so that stdio mode doesn't require these dependencies.
        """
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.types import Receive, Scope, Send

        session_manager = StreamableHTTPSessionManager(
            app=self._server,
            stateless=False,
        )

        class _MCPEndpoint:
            """ASGI app that delegates to the session manager."""

            def __init__(self, mgr: StreamableHTTPSessionManager) -> None:
                self._mgr = mgr

            async def __call__(
                self, scope: Scope, receive: Receive, send: Send
            ) -> None:
                await self._mgr.handle_request(scope, receive, send)

        async def lifespan(app: Starlette):  # type: ignore[no-untyped-def]
            async with session_manager.run():
                yield

        app = Starlette(
            routes=[
                Route(
                    "/mcp",
                    endpoint=_MCPEndpoint(session_manager),
                    methods=["GET", "POST", "DELETE"],
                ),
            ],
            lifespan=lifespan,
        )
        return app, session_manager

    async def serve_http(self, host: str = "127.0.0.1", port: int = 8374) -> None:
        """Serve MCP over Streamable HTTP transport. Call system.start() first.

        Runs a Starlette/uvicorn HTTP server. Clients interact via
        POST/GET/DELETE on /mcp (MCP Streamable HTTP spec).
        """
        import uvicorn

        app, _ = self._build_http_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()

    # ------------------------------------------------------------------
    # Daemon mode: background recording + Streamable HTTP transport
    # ------------------------------------------------------------------

    async def _run_retention_loop(
        self, stop_event: asyncio.Event, interval_hours: float = 6.0
    ) -> None:
        """Run retention cleanup on startup and periodically."""
        store = self._system.store
        if not store:
            return
        while not stop_event.is_set():
            try:
                counts = await run_cleanup(store)
                if counts["downsampled"] or counts["purged"]:
                    logger.info(
                        "retention: downsampled %d, purged %d",
                        counts["downsampled"], counts["purged"],
                    )
            except Exception:
                logger.warning("retention cleanup failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=interval_hours * 3600
                )
                break
            except TimeoutError:
                pass

    async def run_daemon_loops(
        self,
        host: str = "127.0.0.1",
        port: int = 8374,
    ) -> None:
        """Run daemon background tasks: recording, retention, and HTTP server.

        Call ``system.start()`` first. Installs signal handlers for SIGINT/SIGTERM
        to trigger graceful shutdown: sets stop_event so recorder and
        retention loops can finish their current cycle, then tells uvicorn
        to exit, which collapses the TaskGroup cleanly.
        """
        import signal

        import uvicorn

        system = self._system
        if not system.device_manager or not system.store:
            raise RuntimeError("system not started — call system.start() first")

        stop_event = asyncio.Event()
        self._daemon_active = True

        app, _ = self._build_http_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        http_server = uvicorn.Server(config)

        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            """Signal handler: set stop_event and tell uvicorn to exit.

            This lets recorder/retention finish their current poll cycle
            before TaskGroup teardown, instead of hard-cancelling them.
            """
            stop_event.set()
            http_server.should_exit = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_shutdown)

        async def _missing_data_loop() -> None:
            while not stop_event.is_set():
                if system.rule_engine:
                    await system.rule_engine.check_missing(hours=1.0)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=300)
                except TimeoutError:
                    pass

        tasks = [
            run_recorder(system.device_manager, system.store, stop_event, bus=system.event_queue),
            self._run_retention_loop(stop_event),
            _missing_data_loop(),
            http_server.serve(),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            stop_event.set()
            self._daemon_active = False
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
            await system.stop()

    async def run_daemon(
        self,
        host: str = "127.0.0.1",
        port: int = 8374,
    ) -> None:
        """Start the server in daemon mode: background recording + HTTP endpoint.

        Full lifecycle: calls ``system.start()``, runs daemon loops, then ``system.stop()``.
        """
        try:
            await self._system.start()
        except Exception:
            await self._system.stop()
            raise

        await self.run_daemon_loops(host=host, port=port)

    @property
    def system(self) -> SystemManager:
        """The injected SystemManager instance."""
        return self._system
