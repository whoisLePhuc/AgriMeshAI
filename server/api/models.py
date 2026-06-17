"""SQLAlchemy models — Central Server database schema."""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, Float, String, DateTime, ForeignKey, Boolean, UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Farm(Base):
    __tablename__ = "farms"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    name       = Column(String, nullable=False)
    location   = Column(String, nullable=True)
    edge_ip    = Column(String, nullable=True)           # Tailscale IP
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen  = Column(DateTime(timezone=True), nullable=True)


class User(Base):
    __tablename__ = "users"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    username     = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role         = Column(String, default="viewer")       # admin, operator, viewer
    farm_id      = Column(Integer, ForeignKey("farms.id"), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_active    = Column(Boolean, default=True)

    farm = relationship("Farm")


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("farm_id", "node_id"),)

    id        = Column(Integer, primary_key=True, autoincrement=True)
    farm_id   = Column(Integer, ForeignKey("farms.id"), nullable=False)
    node_id   = Column(Integer, nullable=False)           # node_id from Edge
    lora_addr = Column(String, nullable=True)             # hex "0xABCD"
    node_type = Column(String, nullable=False, default="sensor")  # sensor, actuator
    label     = Column(String, nullable=True)             # "Khu A"
    status    = Column(String, default="active")          # active, inactive, offline
    created_at= Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime(timezone=True), nullable=True)

    farm = relationship("Farm")


class Reading(Base):
    __tablename__ = "readings"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    time      = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    farm_id   = Column(Integer, ForeignKey("farms.id"), nullable=False)
    node_id   = Column(Integer, nullable=False)
    sensor_id = Column(Integer, nullable=False)
    value     = Column(Float, nullable=False)
    unit      = Column(String, nullable=False)
    seq       = Column(Integer, nullable=True)            # from SensorReading.seq

    __table_args__ = (
        Index("idx_readings_farm_time", "farm_id", "time"),
        Index("idx_readings_node_sensor", "farm_id", "node_id", "sensor_id", "time"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    farm_id   = Column(Integer, ForeignKey("farms.id"), nullable=False)
    node_id   = Column(Integer, nullable=True)
    rule_id   = Column(String, nullable=False)
    severity  = Column(String, nullable=False)           # CRITICAL, WARNING, INFO
    message   = Column(String, nullable=False)
    value     = Column(Float, nullable=True)
    created_at= Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    ack_at    = Column(DateTime(timezone=True), nullable=True)
    ack_by    = Column(String, nullable=True)

    __table_args__ = (
        Index("idx_alerts_farm_severity", "farm_id", "severity", "created_at"),
    )


class ActuationLog(Base):
    __tablename__ = "actuation_log"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    farm_id      = Column(Integer, ForeignKey("farms.id"), nullable=False)
    node_id      = Column(Integer, nullable=False)
    relay_id     = Column(Integer, nullable=False)
    command      = Column(String, nullable=False)         # ON, OFF
    duration_s   = Column(Integer, default=0)
    triggered_by = Column(String, default="user")
    result       = Column(String, default="pending")      # success, timeout, error
    error_msg    = Column(String, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_actuation_farm_time", "farm_id", "created_at"),
    )
