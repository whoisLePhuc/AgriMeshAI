"""Integration tests: SystemManager start/stop lifecycle.

Verifies: idempotent start/stop, health checks, no resource leaks.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from system.config import Config
from system.manager import SystemManager


pytestmark = pytest.mark.integration


class TestSystemLifecycle:
    """SystemManager start/stop/restart cycles."""

    async def _make_sm(self) -> SystemManager:
        """Create a temporary SystemManager for lifecycle testing."""
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
        # Attach db_path for cleanup
        sm._test_db_path = db_path  # type: ignore[attr-defined]
        return sm

    async def _cleanup(self, sm: SystemManager) -> None:
        """Stop SystemManager and remove temp DB."""
        try:
            await sm.stop()
        finally:
            db_path = getattr(sm, "_test_db_path", None)
            if db_path and os.path.exists(db_path):
                os.unlink(db_path)

    async def test_start_stop_cycle(self) -> None:
        """SystemManager starts and stops without errors."""
        sm = await self._make_sm()
        try:
            await sm.start()
            health = await sm.health()
            for name, status in health.items():
                if name == "device_manager":
                    continue
                assert status.healthy, f"{name}: {status.message}"
        finally:
            await self._cleanup(sm)

    async def test_restart_3_cycles(self) -> None:
        """SystemManager can be started and stopped 3× without state leaks."""
        sm = await self._make_sm()
        try:
            for _ in range(3):
                await sm.start()
                health = await sm.health()
                for name, status in health.items():
                    if name == "device_manager":
                        continue
                    assert status.healthy, f"{name}: {status.message}"
                await sm.stop()
        finally:
            await self._cleanup(sm)

    async def test_double_start_raises(self) -> None:
        """Second start() call raises RuntimeError."""
        sm = await self._make_sm()
        try:
            await sm.start()
            with pytest.raises(RuntimeError, match="already started"):
                await sm.start()
        finally:
            await self._cleanup(sm)

    async def test_stop_before_start_is_safe(self) -> None:
        """stop() before start() does not raise errors."""
        sm = await self._make_sm()
        try:
            await sm.stop()  # should not raise
        finally:
            await self._cleanup(sm)

    async def test_no_task_leak_after_stop(self) -> None:
        """No leftover asyncio tasks after stop()."""
        sm = await self._make_sm()
        current_task = asyncio.current_task()
        tasks_before = {
            t for t in asyncio.all_tasks()
            if t is not current_task
        }
        try:
            await sm.start()
        finally:
            await self._cleanup(sm)
        tasks_after = {
            t for t in asyncio.all_tasks()
            if t is not current_task
        }
        assert tasks_after == tasks_before, f"Task leak: {tasks_after - tasks_before}"
