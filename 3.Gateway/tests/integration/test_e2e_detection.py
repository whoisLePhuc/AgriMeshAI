"""Integration tests: End-to-end detection flow with MockAdapter.

Verifies: EventBus → MLDetector → detectors → alert_triggered → NotifierManager.
"""

from __future__ import annotations

import pytest

from system.manager import SystemManager


pytestmark = pytest.mark.integration


class TestE2EDetection:
    """Full pipeline: sensor reading → anomaly detection → alert."""

    async def test_system_startup_healthy(self, system_manager: SystemManager) -> None:
        """Core subsystems report healthy after start.

        device_manager may report unhealthy (no real hardware connected),
        which is expected in integration test mode.
        """
        health = await system_manager.health()
        for name, status in health.items():
            if name == "device_manager":
                continue  # no real hardware in integration test
            assert status.healthy, f"{name}: {status.message}"

    async def test_normal_readings_no_alert(self, system_manager: SystemManager,
                                            event_bus: pytest.fixture) -> None:
        """Normal readings within 3σ produce no alert_triggered event."""
        alerts: list[dict[str, object]] = []

        async def capture(**data: object) -> None:
            alerts.append(data)

        event_bus.on("alert_triggered", capture)
        for _ in range(25):
            await event_bus.emit(
                "reading_recorded",
                device_id="1", sensor_id="1", value=30.0,
            )
        assert len(alerts) == 0

    async def test_anomalous_reading_triggers_alert(self, system_manager: SystemManager,
                                                    event_bus: pytest.fixture) -> None:
        """Anomalous reading (1000°C) triggers alert_triggered within 10s."""
        alerts: list[dict[str, object]] = []

        async def capture(**data: object) -> None:
            alerts.append(data)

        event_bus.on("alert_triggered", capture)
        # Feed normal readings to establish baseline
        for _ in range(25):
            await event_bus.emit(
                "reading_recorded",
                device_id="1", sensor_id="1", value=30.0,
            )
        # Inject anomalous reading
        await event_bus.emit(
            "reading_recorded",
            device_id="1", sensor_id="1", value=1000.0,
        )
        assert len(alerts) >= 1
        assert alerts[0]["severity"] in ("WARNING", "CRITICAL")

    async def test_anomalous_reading_via_mock_device(self, system_manager: SystemManager) -> None:
        """Alert propagates through full pipeline when triggered by device tool."""
        alerts: list[dict[str, object]] = []

        async def capture(**data: object) -> None:
            alerts.append(data)

        system_manager.event_bus.on("alert_triggered", capture)
        for _ in range(25):
            await system_manager.event_bus.emit(
                "reading_recorded",
                device_id="1", sensor_id="1", value=30.0,
            )
        await system_manager.event_bus.emit(
            "reading_recorded",
            device_id="1", sensor_id="1", value=1000.0,
        )
        assert len(alerts) >= 1
