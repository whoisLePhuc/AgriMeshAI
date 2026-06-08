"""
AgriMeshAI MCP Server — core server with stdio and HTTP transports.
Integrates with recorder, discovery, aggregator.
"""

import os
import json
import yaml
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent, ServerCapabilities
from recorder import Recorder
from mcp_server.discovery import discover_devices
from mcp_server.aggregator import Aggregator


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(ROOT, "devices")


def create_server(recorder: Recorder, profiles_dir: str = PROFILES_DIR):
    """Create and configure the MCP server with fleet + device tools."""
    server = Server("agrimesh-mcp")

    # Discover devices from TOML profiles
    discovered = discover_devices(profiles_dir)

    # Aggregator — manages devices, tool routing, per-device locking
    aggregator = Aggregator()
    aggregator.register_all(discovered)

    from mcp_server.tools.fleet import get_fleet_tools, handle_fleet_tool

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        tools = []
        # Fleet tools (cross-device queries)
        tools.extend(get_fleet_tools())
        # Device tools (via aggregator)
        tools.extend(aggregator.get_tools())
        return tools

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        # 1. Fleet tools
        result = await handle_fleet_tool(name, arguments, recorder)
        if result is not None:
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        # 2. Device tools (via aggregator with per-device locking)
        device_result = await aggregator.call_tool(name)
        if not device_result.success:
            return [TextContent(type="text", text=json.dumps({"error": device_result.error}))]

        # 3. Auto-record numeric readings
        try:
            val = float(device_result.data.strip())
            device_name = name.split(".")[0]
            await recorder.record_reading(
                node_id=hash(device_name) % 1000,
                sensor_id=name.split(".")[1],
                value=val,
                unit="",
            )
        except ValueError:
            pass

        return [TextContent(type="text", text=device_result.data)]

    return server


async def serve_stdio(recorder: Recorder, profiles_dir: str = PROFILES_DIR):
    """Start MCP server in stdio mode (for Agent / Claude Desktop)."""
    server = create_server(recorder, profiles_dir)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream,
            InitializationOptions(
                server_name="agrimesh-mcp",
                server_version="0.1.0",
                capabilities=ServerCapabilities(),
            ),
        )


async def serve_http(recorder: Recorder, host: str = "0.0.0.0", port: int = 8374,
                     profiles_dir: str = PROFILES_DIR):
    """Start MCP server in HTTP daemon mode with SSE transport."""
    server = create_server(recorder, profiles_dir)
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    import uvicorn

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send,
        ) as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream,
                InitializationOptions(
                    server_name="agrimesh-mcp",
                    server_version="0.1.0",
                    capabilities=ServerCapabilities(),
                ),
            )

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
        ],
    )

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    srv = uvicorn.Server(config)
    await srv.serve()
