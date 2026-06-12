// mesh_types.h — AgriMeshAI LoRa Mesh Protocol Types
// Shared constants and packet structs for all 3 firmware types.
#pragma once

#include <cstdint>

// ── Message Type IDs (byte 0 of LoRa payload) ────────────────────

enum MeshMsgType : uint8_t {
    MSG_SENSOR_DATA = 0x01,  // Sensor → Gateway: sensor data (temp, humidity, moisture...)
    MSG_ANNOUNCE     = 0x02,  // Node → all: join/rejoin announcement
    MSG_RELAY_CMD    = 0x10,  // Gateway → Actuator: relay control
    MSG_RELAY_ACK    = 0x11,  // Actuator → Gateway: relay acknowledge
    MSG_RELAY_SYNC   = 0x12,  // Gateway → Actuator: state sync request
    MSG_PING         = 0x20,  // Gateway → Node: alive check
    MSG_PONG         = 0x21,  // Node → Gateway: alive response
};

// ── Sensor ID constants (used in TEMP_READING payload) ───────────

enum SensorId : uint8_t {
    SENSOR_TEMPERATURE = 0,
    SENSOR_HUMIDITY    = 1,
    SENSOR_MOISTURE    = 2,
    SENSOR_LIGHT       = 3,
    SENSOR_BATTERY     = 4,
};

// ── Relay command constants ──────────────────────────────────────

enum RelayCmd : uint8_t {
    RELAY_OFF    = 0,
    RELAY_ON     = 1,
    RELAY_TOGGLE = 2,
};

// ── Node type constants (used in ANNOUNCE payload) ───────────────

enum NodeType : uint8_t {
    NODE_TYPE_SENSOR   = 0,
    NODE_TYPE_ACTUATOR = 1,
};

// ── Addressing ───────────────────────────────────────────────────

constexpr uint16_t GATEWAY_LORA_ADDR = 0x0001;
constexpr uint32_t MAX_ON_DURATION_MS = 30 * 60 * 1000;  // 30 minutes
constexpr uint32_t SENSOR_PUSH_INTERVAL_MS = 60 * 1000;  // 60 seconds
constexpr uint32_t HEARTBEAT_INTERVAL_MS  = 120 * 1000;  // 2 minutes
constexpr uint32_t UART_TIMEOUT_MS = 2000;                // 2 seconds
constexpr uint32_t LORA_ACK_TIMEOUT_MS = 5000;            // 5 seconds
constexpr int      MAX_RETRIES = 3;
constexpr uint16_t UART_RX_BUF_SIZE = 1024;

// ── Packet structs (packed, no padding) ──────────────────────────

#pragma pack(push, 1)

struct SensorReading {
    uint8_t  type      = MSG_SENSOR_DATA;
    uint8_t  sensor_id;        // SensorId
    uint16_t seq;              // sequence number (0-65535, for Edge dedup)
    uint32_t timestamp;        // epoch seconds (when reading was taken)
    float    value;            // IEEE 754 float32, little-endian
};

struct Announce {
    uint8_t  type      = MSG_ANNOUNCE;
    uint8_t  node_type;        // NodeType
    uint8_t  fw_ver;           // major.minor packed (0x10 = v1.0)
};

struct RelayCmdPacket {
    uint8_t  type         = MSG_RELAY_CMD;
    uint8_t  relay_id;          // 0-3
    uint8_t  cmd;               // RelayCmd
    uint32_t duration_ms;       // 0 = indefinite, >0 = auto-off
};

struct RelayAck {
    uint8_t  type      = MSG_RELAY_ACK;
    uint8_t  relay_id;
    uint8_t  state;            // 0=OFF, 1=ON
};

struct Ping {
    uint8_t  type = MSG_PING;
};

struct Pong {
    uint8_t  type         = MSG_PONG;
    uint16_t uptime_hours;      // uptime in hours, little-endian
};

#pragma pack(pop)

// ── Compile-time size checks ─────────────────────────────────────

static_assert(sizeof(SensorReading) == 12, "SensorReading size mismatch");
static_assert(sizeof(Announce) == 3, "Announce size mismatch");
static_assert(sizeof(RelayCmdPacket) == 7, "RelayCmdPacket size mismatch");
static_assert(sizeof(RelayAck) == 3, "RelayAck size mismatch");
static_assert(sizeof(Ping) == 1, "Ping size mismatch");
static_assert(sizeof(Pong) == 3, "Pong size mismatch");
