"""Routes: Edge Gateway → Central Server sync (internal API)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.config import settings
from api.models import Farm, Node, Reading, Alert, ActuationLog
from api.routes.core_routes import TelemetryBatch, RelayResponse

router = APIRouter(prefix="/api/edge", tags=["edge"])

# Shared secret (simple — use JWT in production)
EDGE_API_KEY = "agrimesh-edge-secret"


def verify_edge(api_key: str = "") -> str:
    if api_key != EDGE_API_KEY:
        raise HTTPException(403, "Invalid edge API key")
    return api_key


@router.post("/{farm_id}/telemetry")
async def ingest_telemetry(farm_id: int, batch: TelemetryBatch,
                            api_key: str = Depends(verify_edge),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """Edge Gateway pushes sensor readings to Central."""
    now = datetime.now(timezone.utc)
    count = 0

    for entry in batch.readings:
        reading = Reading(
            time=now, farm_id=farm_id, node_id=entry.node_id,
            sensor_id=entry.sensor_id, value=entry.value, unit=entry.unit,
            seq=entry.seq,
        )
        db.add(reading)
        count += 1

    # Update farm heartbeat
    farm_result = await db.execute(select(Farm).where(Farm.id == farm_id))
    farm = farm_result.scalar_one_or_none()
    if farm:
        farm.last_seen = now

    await db.commit()
    return {"status": "ok", "readings_stored": count}


@router.post("/{farm_id}/heartbeat")
async def heartbeat(farm_id: int, api_key: str = Depends(verify_edge),
                     db: AsyncSession = Depends(get_db)) -> dict:
    """Edge Gateway heartbeat — update last_seen."""
    result = await db.execute(select(Farm).where(Farm.id == farm_id))
    farm = result.scalar_one_or_none()
    if not farm:
        raise HTTPException(404, "Farm not registered")

    farm.last_seen = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "alive", "farm_id": farm_id}


@router.get("/{farm_id}/pending")
async def get_pending_commands(farm_id: int, api_key: str = Depends(verify_edge),
                                db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Edge polls Central for pending relay commands."""
    result = await db.execute(
        select(ActuationLog).where(
            ActuationLog.farm_id == farm_id,
            ActuationLog.result == "pending",
        ).order_by(ActuationLog.created_at).limit(10)
    )
    pending = result.scalars().all()
    commands = []
    for p in pending:
        commands.append({
            "id": p.id, "node_id": p.node_id, "relay_id": p.relay_id,
            "command": p.command, "duration_s": p.duration_s, "triggered_by": p.triggered_by,
        })
    return commands


@router.post("/{farm_id}/pending/{cmd_id}/complete")
async def complete_command(farm_id: int, cmd_id: int, result: str = "success",
                            api_key: str = Depends(verify_edge),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """Edge reports relay command result back to Central."""
    cmd = await db.get(ActuationLog, cmd_id)
    if not cmd or cmd.farm_id != farm_id:
        raise HTTPException(404, "Command not found")
    cmd.result = result
    await db.commit()
    return {"status": "ok", "cmd_id": cmd_id, "result": result}


@router.post("/{farm_id}/nodes/sync")
async def sync_nodes(farm_id: int, nodes: list[dict], api_key: str = Depends(verify_edge),
                      db: AsyncSession = Depends(get_db)) -> dict:
    """Edge pushes current node list to Central (after discovery)."""
    added, updated = 0, 0
    for n in nodes:
        existing = await db.execute(
            select(Node).where(Node.farm_id == farm_id, Node.node_id == n["node_id"])
        )
        node = existing.scalar_one_or_none()
        if node:
            node.lora_addr = n.get("lora_addr")
            node.node_type = n.get("node_type")
            node.active = n.get("active", True)
            node.last_seen = datetime.now(timezone.utc)
            updated += 1
        else:
            db.add(Node(farm_id=farm_id, node_id=n["node_id"],
                        lora_addr=n.get("lora_addr"), node_type=n.get("node_type"),
                        label=n.get("label"), last_seen=datetime.now(timezone.utc)))
            added += 1
    await db.commit()
    return {"added": added, "updated": updated}
