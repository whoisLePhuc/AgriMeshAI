"""EnrichmentPipeline — async queue-based alert enrichment.

Subscribes to ``alert_triggered`` events, appends 24h historical context,
and attempts an LLM-powered natural language explanation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class EnrichmentPipeline:
    """Queue-based alert enrichment with best-effort LLM.

    Usage::
        from ml_detector.enrichment import EnrichmentPipeline

        pipeline = EnrichmentPipeline(store)
        pipeline.start()
        pipeline.enqueue(alert_data)
    """

    def __init__(self, store: Any, llm_api_url: str | None = None) -> None:
        self._store = store
        self._llm_api_url = llm_api_url
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._task: asyncio.Task[None] | None = None
        self._retry_count: dict[int, int] = {}  # id → retry attempts

    def start(self) -> None:
        """Start the background enrichment processing loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._process_queue())
        logger.info("EnrichmentPipeline started")

    async def stop(self) -> None:
        """Stop the background loop."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("EnrichmentPipeline stopped")

    @property
    def queue_size(self) -> int:
        """Number of alerts pending enrichment."""
        return self._queue.qsize()

    def enqueue(
        self,
        alert_id: int,
        device_id: str,
        sensor_id: str,
        severity: str,
        value: float,
        message: str,
        rule_id: str,
    ) -> None:
        """Queue an alert for enrichment. Never blocks."""
        try:
            self._queue.put_nowait({
                "id": alert_id,
                "device_id": device_id,
                "sensor_id": sensor_id,
                "severity": severity,
                "value": value,
                "message": message,
                "rule_id": rule_id,
                "timestamp": time.time(),
            })
        except asyncio.QueueFull:
            logger.warning("enrichment queue full (1000); dropping alert %s", alert_id)

    async def _process_queue(self) -> None:
        """Background loop: dequeue alerts and enrich them."""
        while True:
            try:
                alert = await self._queue.get()
                await self._enrich_one(alert)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("enrichment pipeline error")

    async def _enrich_one(self, alert: dict[str, Any]) -> None:
        """Enrich a single alert: add 24h context, attempt LLM."""
        alert_id = alert["id"]
        device_id = alert["device_id"]
        sensor_id = alert["sensor_id"]

        # Always add historical context
        history = await self._get_history(device_id, sensor_id, hours=24)
        alert["historical_context"] = history
        logger.debug("enriched alert %s with %d history rows", alert_id, len(history))

        # Attempt LLM enrichment (best-effort)
        if self._llm_api_url:
            explanation = await self._call_llm(alert)
            alert["llm_explanation"] = explanation
            if explanation:
                logger.info("LLM enrichment OK for alert %s", alert_id)
                return

        # LLM failed or not configured — track retries
        retries = self._retry_count.get(alert_id, 0)
        if retries < 3:
            self._retry_count[alert_id] = retries + 1
            backoff = [30, 120, 300][retries]
            logger.info("enrichment retry %d/3 for alert %s in %ds", retries + 1, alert_id, backoff)
            await asyncio.sleep(backoff)
            try:
                self._queue.put_nowait(alert)
            except asyncio.QueueFull:
                logger.warning("retry queue full; dropping alert %s", alert_id)
        else:
            logger.warning("enrichment failed after 3 retries for alert %s", alert_id)
            self._retry_count.pop(alert_id, None)

    async def _get_history(self, device_id: str, sensor_id: str, hours: int = 24) -> list[dict[str, Any]]:
        """Query last N hours of readings for a sensor."""
        if self._store is None:
            return []
        try:
            return await self._store.get_history_for_enrichment(device_id, sensor_id, hours)
        except AttributeError:
            logger.warning("store has no get_history_for_enrichment method")
            return []
        except Exception:
            logger.exception("failed to query enrichment history")
            return []

    async def _call_llm(self, alert: dict[str, Any]) -> str | None:
        """Call Ollama for a Vietnamese alert explanation."""
        if not self._llm_api_url:
            return None
        try:
            prompt = self._build_prompt(alert)
            payload = {
                "model": "qwen2.5:7b",
                "messages": [
                    {"role": "system", "content": (
                        "Bạn là trợ lý nông nghiệp. Giải thích cảnh báo "
                        "từ cảm biến và đề xuất hành động. Trả lời tiếng Việt."
                    )},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 256,
            }
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._llm_api_url}/v1/chat/completions",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
            choice = data.get("choices", [{}])[0]
            return choice.get("message", {}).get("content")
        except Exception:
            logger.exception("LLM call failed for alert %s", alert.get("id"))
            return None

    @staticmethod
    def _build_prompt(alert: dict[str, Any]) -> str:
        """Build the enrichment prompt from alert and context."""
        history = alert.get("historical_context", [])
        values = [r.get("value", 0) for r in history if isinstance(r, dict)]
        value_range = ""
        if values:
            value_range = f"Khoảng 24h: {min(values):.1f}–{max(values):.1f} ({len(values)} mẫu)"

        return (
            f"Cảnh báo: {alert.get('rule_id', 'N/A')} - {alert.get('message', 'N/A')}\n"
            f"Thiết bị: {alert.get('device_id', 'N/A')}, "
            f"Cảm biến: {alert.get('sensor_id', 'N/A')}\n"
            f"Mức độ: {alert.get('severity', 'N/A')}\n"
            f"Giá trị: {alert.get('value', 'N/A')}\n"
            f"{value_range}\n\n"
            "Hãy giải thích nguyên nhân và đề xuất hành động."
        )
