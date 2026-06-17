# Database Schema — Edge Gateway SQLite

> Phiên bản: 1.1 | Ngày: 12/06/2026
> Nhóm: Implementation Reference — 🟡 Quan trọng

---

Edge gateway dùng SQLite (WAL mode) để lưu node mapping, sensor readings, actuation log.

## 1. nodes — Node Registry

```sql
CREATE TABLE nodes (
    node_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    lora_addr   TEXT    NOT NULL UNIQUE,  -- hex: "0xABCD"
    node_type   TEXT    NOT NULL,          -- 'sensor', 'actuator'
    fw_ver      TEXT    NOT NULL DEFAULT '1.0',
    status      TEXT    NOT NULL DEFAULT 'active', -- 'active', 'inactive', 'offline'
    label       TEXT,                      -- user-defined name: "Khu A"
    location    TEXT,                      -- optional: "Nha kinh 1"
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT                       -- last push/response time
);

CREATE INDEX idx_nodes_status ON nodes(status);
CREATE INDEX idx_nodes_lora_addr ON nodes(lora_addr);
```

## 2. readings — Sensor Data

```sql
CREATE TABLE IF NOT EXISTS readings (
    timestamp   REAL    NOT NULL,  -- epoch seconds
    node_id     INTEGER NOT NULL,  -- FK → nodes.node_id
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    downsampled INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_readings_node_sensor_time
    ON readings (node_id, sensor_id, timestamp);

CREATE INDEX idx_readings_timestamp ON readings(timestamp);
```

> Migration note: `device_id` trong schema cũ (TEXT) được thay bằng `node_id` (INTEGER FK). Script migration ở mục 6.

## 3. actuation_log — Actuator History

```sql
CREATE TABLE actuation_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     INTEGER NOT NULL,  -- FK → nodes.node_id
    relay_id    INTEGER NOT NULL,  -- 0-3
    command     TEXT    NOT NULL,   -- 'ON', 'OFF'
    duration_s  INTEGER NOT NULL DEFAULT 0,
    triggered_by TEXT   NOT NULL DEFAULT 'user',
    result      TEXT    NOT NULL,   -- 'success', 'timeout', 'error'
    error_msg   TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_actuation_node ON actuation_log(node_id, created_at);
```

## 4. event_log — System Events

```sql
CREATE TABLE event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,  -- 'node_join', 'node_leave', 'uart_error', 'mesh_error'
    node_id     INTEGER,
    message     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

## 5. Queries thường dùng

```sql
-- Node đang online
SELECT node_id, label, node_type FROM nodes 
WHERE status = 'active' AND last_seen > datetime('now', '-5 minutes');

-- Lịch sử actuation gần đây
SELECT * FROM actuation_log 
WHERE node_id = 2 ORDER BY created_at DESC LIMIT 20;

-- Sensor reading mới nhất per node
SELECT r.node_id, r.sensor_id, r.value, r.timestamp
FROM readings r
INNER JOIN (SELECT node_id, sensor_id, MAX(timestamp) as maxts
            FROM readings GROUP BY node_id, sensor_id) latest
ON r.node_id = latest.node_id AND r.sensor_id = latest.sensor_id;
```

## 6. Migration Script (device_id TEXT → node_id INTEGER FK)

```sql
-- Bước 1: Tạo bảng nodes từ dữ liệu readings cũ
INSERT INTO nodes (lora_addr, node_type, status)
SELECT DISTINCT device_id, 'sensor', 'active'
FROM readings
WHERE device_id NOT IN (SELECT lora_addr FROM nodes);

-- Bước 2: Tạo bảng readings mới với FK
CREATE TABLE readings_new (
    timestamp   REAL    NOT NULL,
    node_id     INTEGER NOT NULL,
    sensor_id   TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT    NOT NULL,
    downsampled INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);

-- Bước 3: Copy dữ liệu + map FK
INSERT INTO readings_new
SELECT r.timestamp, n.node_id, r.sensor_id, r.value, r.unit, r.downsampled
FROM readings r
INNER JOIN nodes n ON n.lora_addr = r.device_id;

-- Bước 4: Swap bảng
DROP TABLE readings;
ALTER TABLE readings_new RENAME TO readings;

-- Bước 5: Tạo lại indexes
CREATE INDEX idx_readings_node_sensor_time
    ON readings (node_id, sensor_id, timestamp);
CREATE INDEX idx_readings_timestamp ON readings(timestamp);
```
