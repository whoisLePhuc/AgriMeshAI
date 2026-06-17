"""edge-agent — A minimal, zero-dependency AI agent framework."""

from src.agent import Agent, AgentType, Evaluator, Fallback, Guardrail, Router
from src.providers.bedrock import BedrockProvider
from src.providers.ollama import OllamaProvider
from src.chain import Chain
from src.dotenv import dotenv_values, find_dotenv, load_dotenv
from src.mcp import MCPServer, load_mcp_config
from src.schema import parse_dataclass, schema_from_dataclass
from src.session import Session
from src.template import render_template
from src.tool import Tool, tool
from src.types import (
    AgentStep,
    Message,
    RunResult,
    ToolCall,
    ToolCallRecord,
    ToolResult,
)

__all__ = [
    "Agent",
    "AgentStep",
    "AgentType",
    "BedrockProvider",
    "Chain",
    "Evaluator",
    "Fallback",
    "Guardrail",
    "MCPServer",
    "load_mcp_config",
    "Message",
    "OllamaProvider",
    "Router",
    "RunResult",
    "Session",
    "Tool",
    "ToolCall",
    "ToolCallRecord",
    "ToolResult",
    "dotenv_values",
    "find_dotenv",
    "load_dotenv",
    "parse_dataclass",
    "render_template",
    "schema_from_dataclass",
    "tool",
]
