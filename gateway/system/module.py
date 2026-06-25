"""Base module interface for all system modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class HealthStatus:
    """Health check result for a module."""

    healthy: bool
    message: str = ""


class Module(ABC):
    """Base class for all system modules.

    Subclasses implement start/stop for lifecycle and health for status.
    Lightweight setup goes in __init__.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the module. Called once by SystemManager.start()."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop and cleanup. Called once by SystemManager.stop()."""
        ...

    @abstractmethod
    async def health(self) -> HealthStatus:
        """Return current health status."""
        ...
