"""
Pydantic models for device profile definitions.
"""

from pydantic import BaseModel
from typing import Optional


class ToolParam(BaseModel):
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False


class ToolReturns(BaseModel):
    type: str = "string"
    description: str = ""


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    command: str = ""
    params: list[ToolParam] = []
    returns: ToolReturns = ToolReturns()


class HealthConfig(BaseModel):
    check_command: str = ""
    expected: str = ""
    interval_ms: int = 60000


class RecordingConfig(BaseModel):
    enabled: bool = True
    poll_interval_ms: int = 300000


class ConnectionConfig(BaseModel):
    protocol: str = "mock"  # mock | serial | mqtt
    port: str = ""
    baud_rate: int = 115200
    timeout_ms: int = 3000


class DeviceModel(BaseModel):
    name: str
    description: str = ""
    connection: ConnectionConfig = ConnectionConfig()
    tools: list[ToolDefinition] = []
    health: HealthConfig = HealthConfig()
    recording: RecordingConfig = RecordingConfig()
