"""Tests for EnrichmentPipeline."""

from __future__ import annotations

import pytest

from ml_detector.enrichment import EnrichmentPipeline


class TestEnrichmentPipeline:
    """Enrichment pipeline — queue, history, retry."""

    @pytest.fixture
    def pipeline(self) -> EnrichmentPipeline:
        """Pipeline without LLM (offline mode)."""
        return EnrichmentPipeline(store=None, llm_api_url=None)

    def test_enqueue_and_queue_size(self, pipeline: EnrichmentPipeline) -> None:
        """Enqueuing an alert increases queue size."""
        assert pipeline.queue_size == 0
        pipeline.enqueue(alert_id=1, device_id="dev1", sensor_id="s1",
                         severity="WARNING", value=35.0,
                         message="test alert", rule_id="M01")
        assert pipeline.queue_size == 1

    def test_queue_maxsize_1000(self) -> None:
        """Queue does not grow beyond maxsize."""
        p = EnrichmentPipeline(store=None, llm_api_url=None)
        for i in range(1000):
            p.enqueue(alert_id=i, device_id="dev1", sensor_id="s1",
                      severity="INFO", value=float(i),
                      message=f"alert {i}", rule_id="M01")
        assert p.queue_size == 1000
        # One more should be silently dropped
        p.enqueue(alert_id=1001, device_id="dev1", sensor_id="s1",
                  severity="INFO", value=1001.0,
                  message="overflow", rule_id="M01")
        assert p.queue_size == 1000

    def test_retry_count_starts_empty(self, pipeline: EnrichmentPipeline) -> None:
        """No retries tracked initially."""
        assert len(pipeline._retry_count) == 0

    def test_no_llm_no_crash(self, pipeline: EnrichmentPipeline) -> None:
        """Pipeline without LLM configured does not crash."""
        pipeline.enqueue(alert_id=1, device_id="dev1", sensor_id="s1",
                         severity="WARNING", value=35.0,
                         message="test", rule_id="M01")
        # No crash = pass

    async def test_get_history_without_store_returns_empty(self) -> None:
        """get_history returns [] when store is None."""
        p = EnrichmentPipeline(store=None, llm_api_url=None)
        history = await p._get_history("dev1", "s1", hours=24)
        assert history == []

    async def test_enrich_one_without_llm(self, pipeline: EnrichmentPipeline) -> None:
        """_enrich_one does not crash without LLM."""
        alert = {
            "id": 1, "device_id": "dev1", "sensor_id": "s1",
            "severity": "WARNING", "value": 35.0, "message": "test",
            "rule_id": "M01", "timestamp": 1000.0,
        }
        await pipeline._enrich_one(alert)
        # No crash = pass

    def test_build_prompt_contains_alert_info(self) -> None:
        """Prompt string includes key alert fields."""
        alert = {
            "rule_id": "M01", "message": "temperature spike",
            "device_id": "dev1", "sensor_id": "s1",
            "severity": "CRITICAL", "value": 45.0,
            "historical_context": [],
        }
        prompt = EnrichmentPipeline._build_prompt(alert)
        assert "M01" in prompt
        assert "dev1" in prompt
        assert "CRITICAL" in prompt
        assert "45.0" in prompt

    def test_build_prompt_includes_history_range(self) -> None:
        """Prompt includes min/max from historical context."""
        alert = {
            "rule_id": "M01", "message": "spike",
            "device_id": "dev1", "sensor_id": "s1",
            "severity": "WARNING", "value": 45.0,
            "historical_context": [
                {"timestamp": 1000.0, "value": 30.0, "unit": "C"},
                {"timestamp": 2000.0, "value": 32.0, "unit": "C"},
            ],
        }
        prompt = EnrichmentPipeline._build_prompt(alert)
        assert "30.0" in prompt
        assert "32.0" in prompt
