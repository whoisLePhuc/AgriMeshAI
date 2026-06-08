"""
MCP Tool generator — converts DeviceModel to MCP Tool definitions.
"""

from mcp.types import Tool
from mcp_server.devices.model import DeviceModel


def generate_tools(device: DeviceModel) -> list[Tool]:
    """Convert a DeviceModel's tools to MCP Tool definitions (namespaced)."""
    tools = []
    for t in device.tools:
        properties = {}
        required = []
        for p in t.params:
            properties[p.name] = {"type": p.type, "description": p.description}
            if p.required:
                required.append(p.name)

        tools.append(Tool(
            name=f"{device.name}.{t.name}",
            description=t.description,
            inputSchema={
                "type": "object",
                "properties": properties,
                "required": required if required else None,
            },
        ))
    return tools
