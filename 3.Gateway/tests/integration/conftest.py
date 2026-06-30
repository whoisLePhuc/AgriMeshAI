"""Fixtures for integration tests — MockAdapter-based full gateway stack."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
import pytest

from event_bus import EventBus
from system.config import Config
from system.manager import SystemManager


def ollama_available(url: str = "http://localhost:11434") -> bool:
    """Check if Ollama server is reachable."""
    try:
        r = httpx.get(f"{url}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture
async def system_manager() -> SystemManager:
    """Create a SystemManager with temp SQLite DB and mock device profile.

    The fixture:
    1. Creates a temp SQLite DB file
    2. Configures SystemManager to use tests/fixtures/ for device profiles
    3. Starts SystemManager (wires all modules)
    4. Yields the running SystemManager
    5. Stops and cleans up on teardown
    """
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
    config_dir = Path(__file__).resolve().parent.parent.parent / "config"

    config = Config(
        config_dir=str(config_dir),
        profiles_dir=str(fixtures_dir),
        db_path=db_path,
        rules_path=str(config_dir / "rules.yaml"),
        notifiers_path=str(config_dir / "notifiers.yaml"),
    )
    sm = SystemManager(config)

    try:
        await sm.start()
        yield sm
    finally:
        await sm.stop()
        os.unlink(db_path)


@pytest.fixture
def event_bus(system_manager: SystemManager) -> EventBus:
    """Reference to the running SystemManager's EventBus."""
    return system_manager.event_bus
