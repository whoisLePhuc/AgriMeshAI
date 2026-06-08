#!/usr/bin/env python3
"""AgriMeshAI — AI Agent for Smart Agriculture.

Powered by edge-agent framework (zero-dependency).
Connects to agrimesh MCP server for hardware tool access.
"""

import os
import sys
import yaml
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ensure vendored edge_agent is importable
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# Config
with open(os.path.join(ROOT, "config", "models.yaml")) as f:
    config = yaml.safe_load(f)

model_name = config["llm"]["model"]
api_url = config["llm"]["api_url"]
# OllamaProvider appends /v1/chat/completions internally
base_url = api_url.rstrip("/v1").rstrip("/")

instructions = Path(os.path.join(AGENT_DIR, "instructions.txt"))

# Edge-agent imports (vendored, zero dependency)
from edge_agent import Agent
from edge_agent.providers import OllamaProvider
from edge_agent.mcp import MCPServer
from edge_agent.session import Session

# MCP server: agrimesh exposes hardware tools via stdio
mcp_server = MCPServer(
    "agrimesh-mcp",
    command=[sys.executable, "-m", "mcp_server", "start"],
)

with mcp_server:
    agent = Agent(
        provider=OllamaProvider(
            model=model_name,
            base_url=base_url,
            temperature=0.01,
        ),
        instructions=instructions,
        mcp_servers=[mcp_server],
    )

    print(f"✓ Agent ready (model: {model_name})")
    print(f"  MCP: agrimesh-mcp ({len(mcp_server.tools)} tools)")
    print(f"  Type 'exit' to quit\n")

    Session(agent=agent).start()
