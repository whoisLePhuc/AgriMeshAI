"""Integration tests: Real LLM enrichment with Ollama.

Verifies: EnrichmentPipeline connects to Ollama, receives Vietnamese explanation.
Tests skip gracefully if Ollama is not reachable.
"""

from __future__ import annotations

import os

import pytest

from ml_detector.enrichment import EnrichmentPipeline
from tests.integration.conftest import ollama_available

pytestmark = pytest.mark.integration

_OLLAMA_URL = os.environ.get(
    "OLLAMA_URL",
    "http://localhost:11434",
)


class TestLLMEnrichment:
    """Live Ollama enrichment integration."""

    @pytest.mark.skipif(not ollama_available(_OLLAMA_URL), reason="Ollama not running")
    async def test_ollama_reachable(self) -> None:
        """Ollama server responds with model list containing qwen2.5."""
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_OLLAMA_URL}/api/tags")
            assert resp.status_code == 200
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            assert any("qwen2.5" in name for name in model_names), \
                f"qwen2.5 not found in models: {model_names}"

    @pytest.mark.skipif(not ollama_available(_OLLAMA_URL), reason="Ollama not running")
    async def test_enrichment_vietnamese_response(self) -> None:
        """EnrichmentPipeline produces Vietnamese explanation from Ollama."""
        pipeline = EnrichmentPipeline(
            store=None,
            llm_api_url=_OLLAMA_URL,
        )
        pipeline.enqueue(
            alert_id=42,
            device_id="test_device",
            sensor_id="temperature",
            severity="CRITICAL",
            value=45.0,
            message="M01 Node 1 sensor 1: value 45.00 deviates 7.2σ from baseline 30.00",
            rule_id="M01",
        )
        alert_data = await pipeline._get_history("test_device", "temperature", hours=24)
        assert alert_data == []  # No store configured

        # Build and call LLM directly
        alert = {
            "id": 42,
            "device_id": "test_device",
            "sensor_id": "temperature",
            "severity": "CRITICAL",
            "value": 45.0,
            "message": "Nhiệt độ tăng cao bất thường",
            "rule_id": "M01",
            "historical_context": [],
        }
        response = await pipeline._call_llm(alert)
        assert response is not None, "LLM returned None"
        assert len(response) > 0, "LLM returned empty response"
        # Verify Vietnamese content
        vietnamese_markers = ["Cảnh báo", "cảnh báo", "đề xuất", "kiểm tra", "nhiệt độ"]
        assert any(marker in response for marker in vietnamese_markers), \
            f"No Vietnamese content found in: {response[:200]}"
