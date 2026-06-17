"""Routes: farms, telemetry, edge sync, relays."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.config import settings
from api.models import Farm, Node, Reading, Alert, ActuationLog, User

router = APIRouter(prefix="/api", tags=["core"])

# ── Pydantic schemas ──────────────────────────────────────────────

class TelemetryEntry(BaseModel):
    node_id: int
    sensor_id: int
    value: float
    unit: str
    timestamp: Optional[str] = None
    seq: Optional[int] = None

class TelemetryBatch(BaseModel):
    readings: list[TelemetryEntry]

class RelayCommand(BaseModel):
    node_id: int
    relay_id: int
    state: int          # 0=OFF, 1=ON
    duration_s: int = 0

class RelayResponse(BaseModel):
    node_id: int
    relay_id: int
    state: str          # ON / OFF
    result: str         # success / pending

# ── Farms ─────────────────────────────────────────────────────────

class FarmCreate(BaseModel):
    name: str
    location: Optional[str] = None
    edge_ip: Optional[str] = None

@router.post("/farms", status_code=201)
async def create_farm(farm: FarmCreate, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)) -> dict:
    if user.role not in ("admin", "operator"):
        raise HTTPException(403, "Requires admin or operator role")
    new_farm = Farm(name=farm.name, location=farm.location, edge_ip=farm.edge_ip)
    db.add(new_farm)
    await db.commit()
    await db.refresh(new_farm)
    return {"id": new_farm.id, "name": new_farm.name}

@router.get("/farms")
async def list_farms(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    if user.role == "admin":
        result = await db.execute(select(Farm).order_by(Farm.name))
    elif user.farm_id:
        result = await db.execute(select(Farm).where(Farm.id == user.farm_id))
    else:
        return []

    farms = result.scalars().all()
    return [{"id": f.id, "name": f.name, "location": f.location,
             "last_seen": f.last_seen.isoformat() if f.last_seen else None} for f in farms]

@router.get("/farms/{farm_id}")
async def get_farm(farm_id: int, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Farm).where(Farm.id == farm_id))
    farm = result.scalar_one_or_none()
    if not farm:
        raise HTTPException(404, "Farm not found")
    return {"id": farm.id, "name": farm.name, "location": farm.location,
            "edge_ip": farm.edge_ip, "created_at": farm.created_at.isoformat() if farm.created_at else None,
            "last_seen": farm.last_seen.isoformat() if farm.last_seen else None}

# ── Nodes ─────────────────────────────────────────────────────────

@router.get("/farms/{farm_id}/nodes")
async def list_nodes(farm_id: int, user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(Node).where(Node.farm_id == farm_id).order_by(Node.node_id))
    nodes = result.scalars().all()
    return [{"id": n.id, "node_id": n.node_id, "node_type": n.node_type,
             "label": n.label, "status": n.status, "lora_addr": n.lora_addr,
             "last_seen": n.last_seen.isoformat() if n.last_seen else None} for n in nodes]

# ── Sensor Readings ───────────────────────────────────────────────

@router.get("/farms/{farm_id}/readings")
async def get_readings(farm_id: int,
                        node_id: Optional[int] = None,
                        sensor_id: Optional[int] = None,
                        hours: int = Query(default=24, le=720),
                        limit: int = Query(default=500, le=5000),
                        user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)) -> list[dict]:
    q = select(Reading).where(Reading.farm_id == farm_id)
    if node_id is not None:
        q = q.where(Reading.node_id == node_id)
    if sensor_id is not None:
        q = q.where(Reading.sensor_id == sensor_id)

    cutoff = datetime.now(timezone.utc).replace(microsecond=0)
    q = q.where(Reading.time >= cutoff).order_by(desc(Reading.time)).limit(limit)
    result = await db.execute(q)
    readings = result.scalars().all()
    return [{"time": r.time.isoformat(), "node_id": r.node_id, "sensor_id": r.sensor_id,
             "value": r.value, "unit": r.unit, "seq": r.seq} for r in readings]

@router.get("/farms/{farm_id}/readings/latest")
async def get_latest_readings(farm_id: int, user: User = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)) -> list[dict]:
    sub = (select(Reading.node_id, Reading.sensor_id, func.max(Reading.time).label("maxtime"))
           .where(Reading.farm_id == farm_id)
           .group_by(Reading.node_id, Reading.sensor_id)).alias("sub")
    q = select(Reading).join(sub, (Reading.node_id == sub.c.node_id) &
                                  (Reading.sensor_id == sub.c.sensor_id) &
                                  (Reading.time == sub.c.maxtime))
    result = await db.execute(q)
    readings = result.scalars().all()
    return [{"time": r.time.isoformat(), "node_id": r.node_id, "sensor_id": r.sensor_id,
             "value": r.value, "unit": r.unit} for r in readings]

# ── Alerts ────────────────────────────────────────────────────────

@router.get("/farms/{farm_id}/alerts")
async def get_alerts(farm_id: int, severity: Optional[str] = None, hours: int = Query(default=24),
                     limit: int = Query(default=100), user: User = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)) -> list[dict]:
    q = select(Alert).where(Alert.farm_id == farm_id)
    if severity:
        q = q.where(Alert.severity == severity.upper())
    q = q.order_by(desc(Alert.created_at)).limit(limit)
    result = await db.execute(q)
    alerts = result.scalars().all()
    return [{"id": a.id, "rule_id": a.rule_id, "severity": a.severity, "message": a.message,
             "node_id": a.node_id, "value": a.value, "created_at": a.created_at.isoformat(),
             "ack_at": a.ack_at.isoformat() if a.ack_at else None} for a in alerts]

# ── Relay Control ─────────────────────────────────────────────────

@router.post("/farms/{farm_id}/relay", response_model=RelayResponse)
async def control_relay(farm_id: int, cmd: RelayCommand,
                         user: User = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)) -> RelayResponse:
    # Require operator or admin role for actuation
    if user.role not in ("operator", "admin"):
        raise HTTPException(403, "Requires operator or admin role")

    if cmd.state not in (0, 1):
        raise HTTPException(400, "state must be 0 (OFF) or 1 (ON)")

    log = ActuationLog(farm_id=farm_id, node_id=cmd.node_id, relay_id=cmd.relay_id,
                       command="ON" if cmd.state else "OFF", duration_s=cmd.duration_s,
                       triggered_by=user.username, result="pending")
    db.add(log)
    await db.commit()

    # The actual relay command is sent via Edge Gateway.
    # This endpoint records the intent; Edge polls GET /pending for commands.
    return RelayResponse(node_id=cmd.node_id, relay_id=cmd.relay_id,
                         state="ON" if cmd.state else "OFF", result="pending")

# ── Dashboard Summary ─────────────────────────────────────────────

@router.get("/farms/{farm_id}/dashboard")
async def dashboard(farm_id: int, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_db)) -> dict:
    farm_result = await db.execute(select(Farm).where(Farm.id == farm_id))
    farm = farm_result.scalar_one_or_none()
    if not farm:
        raise HTTPException(404, "Farm not found")

    nodes_result = await db.execute(select(Node).where(Node.farm_id == farm_id))
    nodes = nodes_result.scalars().all()

    latest = await db.execute(
        select(Reading.node_id, Reading.sensor_id, Reading.value, Reading.unit)
        .where(Reading.farm_id == farm_id)
        .order_by(desc(Reading.time)).limit(50)
    )
    latest_readings = [{"node_id": r.node_id, "sensor_id": r.sensor_id,
                         "value": r.value, "unit": r.unit} for r in latest.all()]

    recent_alerts = await db.execute(
        select(Alert).where(Alert.farm_id == farm_id)
        .order_by(desc(Alert.created_at)).limit(20)
    )

    return {
        "farm": {"name": farm.name, "location": farm.location, "last_seen": farm.last_seen.isoformat() if farm.last_seen else None},
        "node_count": len(nodes),
        "latest_readings": latest_readings,
        "recent_alerts": [{"severity": a.severity, "message": a.message, "node_id": a.node_id,
                            "time": a.created_at.isoformat()} for a in recent_alerts.scalars().all()]
    }
