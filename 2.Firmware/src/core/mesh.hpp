// mesh.hpp — LoRa mesh abstraction layer.
// Wraps LoRaMesher library: init, send, receive callback, retry queue, announce.
// Shared by sensor, actuator, and gateway firmware.
#pragma once

#include <Arduino.h>
#include <memory>
#include <deque>
#include <vector>
#include "esp_task_wdt.h"
#include "loramesher.hpp"
#include "mesh_types.h"
#include "common/log.hpp"

using namespace loramesher;

// ── Retry queue ───────────────────────────────────────────────────
struct PendingPacket {
    AddressType          dst;
    std::vector<uint8_t> payload;
    uint8_t              retries;
};

extern std::deque<PendingPacket> g_retry_queue;
extern portMUX_TYPE              g_queue_mux;

// ── Ping flag (spinlock-protected for dual-core) ──────────────────
extern portMUX_TYPE g_ping_mux;
extern bool         g_flag_ping_valid;
extern AddressType  g_ping_src;

// ── Mesh state ───────────────────────────────────────────────────
extern std::unique_ptr<LoraMesher> g_mesher;
extern bool                        g_mesh_ready;

// ── API ──────────────────────────────────────────────────────────

// Initialize LoRaMesher with standard AgriMeshAI config (SX1262, 868MHz)
// Calls the given callback for incoming data.
void mesh_init(void (*on_data)(AddressType src, const std::vector<uint8_t>& data));

// Get this node's LoRa address
inline AddressType mesh_get_address() {
    return g_mesher ? g_mesher->GetNodeAddress() : 0;
}

// Send data to destination. Queues on failure (retry up to MAX_RETRIES times).
void mesh_send(AddressType dst, const std::vector<uint8_t>& payload);

// Flush one packet from retry queue. Call periodically in loop().
void mesh_flush_retry_queue();

// Handle incoming ping flag (set by callback, dispatched in loop).
// Returns true if a PONG should be sent, sets pong_dst to the target address.
bool mesh_handle_ping(AddressType& pong_dst);

// Clear ping flag (call after sending PONG).
void mesh_clear_ping();

// Send announce message for this node type.
void mesh_send_announce(uint8_t node_type, uint8_t fw_ver = 0x10);

// Check if mesh is initialized and ready.
inline bool mesh_is_ready() { return g_mesh_ready; }
