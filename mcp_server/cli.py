"""
AgriMeshAI MCP CLI — agrimesh command.

Usage:
    agrimesh start              # stdio mode (connect with AI Agent)
    agrimesh daemon             # HTTP daemon mode
    agrimesh status             # show system status
"""

import os
import sys
import asyncio
import logging
import click
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stderr,
)


def load_config():
    with open(os.path.join(ROOT, "config", "models.yaml")) as f:
        return yaml.safe_load(f)


@click.group()
def main():
    """AgriMeshAI MCP Server — IoT tool orchestration for smart agriculture"""
    pass


@main.command()
def start():
    """Start MCP server in stdio mode (for AI Agent / Claude Desktop)"""
    from mcp_server.server import server

    config = load_config()
    print(f"✓ AgriMesh MCP Server ready (model: {config['llm']['model']})", file=sys.stderr)
    server.run(transport="stdio")


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8374, help="HTTP port")
def daemon(host, port):
    """Start MCP server in HTTP daemon mode"""
    from mcp_server.server import server

    config = load_config()
    print(f"✓ AgriMesh MCP Daemon starting on {host}:{port}", file=sys.stderr)
    print(f"  Model: {config['llm']['model']}", file=sys.stderr)
    print(f"  SSE: http://{host}:{port}/sse", file=sys.stderr)
    server.run(transport="sse", host=host, port=port)


@main.command()
def status():
    """Show system status: Ollama, database, devices"""
    config = load_config()
    model = config["llm"]["model"]
    print(f"Model:         {model}")
    print(f"Ollama URL:    {config['llm']['api_url']}")

    # Check Ollama
    import httpx
    api_base = config['llm']['api_url'].rstrip('/v1').rstrip('/')
    try:
        r = httpx.get(f"{api_base}/api/version", timeout=3)
        print(f"Ollama API:    ✅ {r.json()['version']}")
    except Exception:
        print("Ollama API:    ❌ Not responding")

    # Check database
    import aiosqlite
    db_path = os.path.join(DATA_DIR, "agrimesh.db")

    async def check_db():
        if os.path.exists(db_path):
            db = await aiosqlite.connect(db_path)
            devices_count = await db.execute_fetchall("SELECT COUNT(*) FROM devices")
            readings_count = await db.execute_fetchall("SELECT COUNT(*) FROM readings")
            await db.close()
            size = os.path.getsize(db_path)
            print(f"Database:      ✅ {size / 1024:.1f} KB")
            print(f"Devices:       {devices_count[0][0]} registered")
            print(f"Readings:      {readings_count[0][0]} records")
        else:
            print("Database:      ❌ Not found (run 'agrimesh daemon' first)")

    asyncio.run(check_db())
