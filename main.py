#!/usr/bin/env python3
"""AgriMeshAI — Unified launcher for Jetson board.

Single entry point that initialises all services in one process.
No subprocess, no separate daemon — everything runs together.

Usage:
    python main.py                  Interactive agent (REPL + LLM via Tailscale)
    python main.py daemon           24/7 background recorder + HTTP API
    python main.py status           Quick system status
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(ROOT, "agent"))          # edge-agent
sys.path.insert(0, ROOT)  # mcp_server


# ── async → sync bridge ─────────────────────────────────────────────────────

def _sync_call(loop: asyncio.AbstractEventLoop, coro):
    """Run an async coroutine from sync code using a dedicated loop."""
    return loop.run_until_complete(coro)


def _build_tool_bridge(system, loop: asyncio.AbstractEventLoop) -> list:
    """Convert SystemManager's tools into edge-agent Tool objects.

    Each tool proxies directly to ``system.call_tool()`` — no
    subprocess, no JSON-RPC, no MCP transport overhead.
    """
    from src.tool import Tool as EdgeTool
    from mcp.types import Tool as MCPTool

    mcp_tools: list[MCPTool] = system.list_tools()
    tools: list[EdgeTool] = []

    for t in mcp_tools:
        tool_name = t.name
        desc = t.description or ""

        def _make_tool(name: str, description: str, schema: dict) -> EdgeTool:
            def fn(**kwargs: object) -> str:
                result = _sync_call(loop, system.call_tool(name, kwargs))
                if result.success:
                    return str(result.data) if result.data else ""
                return f"Error: {result.error or 'unknown error'}"

            return EdgeTool(fn=fn, name=name, description=description, parameters=schema)

        tools.append(_make_tool(tool_name, desc, t.inputSchema))

    return tools


# ── persistence ──────────────────────────────────────────────────────────────

def _save_conversation(messages: list) -> None:
    """Append conversation to a plain text log file."""
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    path = os.path.join(ROOT, "data", "conversations.log")
    with open(path, "a", encoding="utf-8") as f:
        for m in messages:
            role = m.get("role", "?")
            content = m.get("content", "")
            if content:
                f.write(f"[{role}] {content}\n")
        f.write("---\n")


# ── modes ────────────────────────────────────────────────────────────────────

def run_agent(
    profiles_dir: str = "device_manager/device_profiles",
    db_path: str = "data/agrimesh.db",
    rules_path: str = "config/rules.yaml",
    notifiers_path: str = "config/notifiers.yaml",
) -> None:
    """Start the AI agent with an in-process AgriMeshAI gateway.

    Loads the system, bridges all tools directly into the edge-agent,
    and starts the interactive REPL.  No subprocess, no stdio MCP.
    """
    import yaml
    from system import Config, SystemManager
    from mcp_server.server import AgriMeshAIServer

    # 1. start gateway
    config = Config(
        profiles_dir=profiles_dir,
        db_path=db_path,
        rules_path=rules_path,
        notifiers_path=notifiers_path,
    )
    system = SystemManager(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        _sync_call(loop, system.start())
    except KeyboardInterrupt:
        print("\nShutting down.")
        _sync_call(loop, system.stop())
        return

    device_count = len(system.device_manager.device_names) if system.device_manager else 0
    tool_count = len(system.list_tools())
    print(f"✓ Gateway ready — {device_count} device(s), {tool_count} tool(s)")

    # 2. bridge tools
    tools = _build_tool_bridge(system, loop)

    # 3. start agent
    config_path = os.path.join(ROOT, "config", "models.yaml")
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    model_name = cfg["llm"]["model"]
    base_url = cfg["llm"]["api_url"].rstrip("/v1").rstrip("/")

    from src import Agent
    from src.providers import OllamaProvider
    from src.session import Session

    agent = Agent(
        provider=OllamaProvider(model=model_name, base_url=base_url, temperature=0.01),
        instructions=Path(os.path.join(ROOT, "agent", "instructions.txt")),
        tools=tools,
    )

    print(f"✓ Agent ready (model: {model_name})")
    print(f"  Type 'exit' to quit\n")

    try:
        Session(agent=agent).start()
    except KeyboardInterrupt:
        pass

    # 4. shutdown
    _sync_call(loop, system.stop())
    loop.close()
    print("Goodbye!")


def run_daemon(
    profiles_dir: str = "device_manager/device_profiles",
    db_path: str = "data/agrimesh.db",
    rules_path: str = "config/rules.yaml",
    notifiers_path: str = "config/notifiers.yaml",
    host: str = "127.0.0.1",
    port: int = 8374,
) -> None:
    """Run as background daemon: recording + HTTP endpoint.

    Polls sensors 24/7 and serves MCP over HTTP.
    """
    from system import Config, SystemManager
    from mcp_server.server import AgriMeshAIServer

    config = Config(
        profiles_dir=profiles_dir,
        db_path=db_path,
        rules_path=rules_path,
        notifiers_path=notifiers_path,
    )
    system = SystemManager(config)
    server = AgriMeshAIServer(system)

    async def _run():
        discovery = await system.start()
        for path, error in discovery.errors:
            print(f"  ⚠ Skipped {path.name}: {error}", file=sys.stderr)

        device_count = len(discovery.devices)
        if device_count == 0:
            print("No devices found. Add TOML device configs.", file=sys.stderr)
            await system.stop()
            return

        tool_count = len(system.list_tools())
        print(f"✓ Discovered {device_count} device(s), exposing {tool_count} tools")
        print(f"✓ MCP server ready on http://{host}:{port}/mcp")
        print(f"  Ctrl+C to stop\n")

        await server.run_daemon_loops(host=host, port=port)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\nShutting down.")


def run_status(
    profiles_dir: str = "device_manager/device_profiles",
    db_path: str = "data/agrimesh.db",
    rules_path: str = "config/rules.yaml",
    notifiers_path: str = "config/notifiers.yaml",
) -> None:
    """Quick status check — shows devices and health."""
    from system import Config, SystemManager

    config = Config(
        profiles_dir=profiles_dir,
        db_path=db_path,
        rules_path=rules_path,
        notifiers_path=notifiers_path,
    )
    system = SystemManager(config)

    for path, error in system.device_manager.catalog.errors:
        print(f"  ⚠ {path.name}: {error}")

    if not system.device_manager.catalog.devices:
        print("No devices found.")
        return

    async def _check():
        await system.start()
        await system.device_manager.health_check_all()
        print(f"Devices ({len(system.device_manager.catalog.devices)}):\n")
        for name in system.device_manager.device_names:
            ds = system.device_manager.get_status(name)
            if ds is None:
                continue
            proto = ds.device.model.connection.protocol
            if ds.connected and ds.healthy is True:
                print(f"  ✓ {name} [{proto}] — healthy")
            elif ds.connected:
                print(f"  ~ {name} [{proto}] — connected (unhealthy or not checked)")
            else:
                print(f"  ✗ {name} [{proto}] — {ds.error or 'unknown'}")
        await system.stop()

    asyncio.run(_check())


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "agent"

    # common options
    profiles_dir = os.path.join(ROOT, "device_manager", "device_profiles")
    db_path = os.path.join(ROOT, "data", "agrimesh.db")
    rules_path = os.path.join(ROOT, "config", "rules.yaml")
    notifiers_path = os.path.join(ROOT, "config", "notifiers.yaml")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    if mode == "agent":
        run_agent(
            profiles_dir=profiles_dir,
            db_path=db_path,
            rules_path=rules_path,
            notifiers_path=notifiers_path,
        )
    elif mode == "daemon":
        host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
        port = int(sys.argv[3]) if len(sys.argv) > 3 else 8374
        run_daemon(
            profiles_dir=profiles_dir,
            db_path=db_path,
            rules_path=rules_path,
            notifiers_path=notifiers_path,
            host=host,
            port=port,
        )
    elif mode == "status":
        run_status(
            profiles_dir=profiles_dir,
            db_path=db_path,
            rules_path=rules_path,
            notifiers_path=notifiers_path,
        )
    else:
        print(f"Usage: python main.py [agent|daemon|status]")
        print(f"  default: agent")
        sys.exit(1)


if __name__ == "__main__":
    main()
