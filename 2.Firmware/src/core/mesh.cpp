// mesh.cpp — LoRa mesh implementation.
#include "core/mesh.hpp"

// ── Global state ──────────────────────────────────────────────────
std::unique_ptr<LoraMesher> g_mesher;
bool                        g_mesh_ready = false;
std::deque<PendingPacket>   g_retry_queue;
portMUX_TYPE                g_queue_mux = portMUX_INITIALIZER_UNLOCKED;
portMUX_TYPE                g_ping_mux  = portMUX_INITIALIZER_UNLOCKED;
bool                        g_flag_ping_valid = false;
AddressType                 g_ping_src = 0;

// ── Radio config (shared by all node types) ───────────────────────
static PinConfig _pin_config(uint8_t cs, uint8_t rst, uint8_t irq,
                              uint8_t io1, uint8_t sck, uint8_t miso, uint8_t mosi) {
    return PinConfig(cs, rst, irq, io1, sck, miso, mosi);
}

void mesh_init(void (*on_data)(AddressType, const std::vector<uint8_t>&)) {
    // Standard AgriMeshAI pinout (Heltec WiFi LoRa 32 V3)
    PinConfig pc(8, 12, 14, 13, 9, 11, 10);
    RadioConfig rc(RadioType::kSx1262, 868.0F, 12, 125.0F, 7, 14);
    rc.setTcxoVoltage(1.8F);
    LoRaMeshProtocolConfig mc;

    g_mesher = LoraMesher::Builder()
                   .withRadioConfig(rc)
                   .withPinConfig(pc)
                   .withLoRaMeshProtocol(mc)
                   .Build();

    if (on_data) {
        g_mesher->SetDataCallback(on_data);
    }

    Result r = g_mesher->Start();
    if (!r) {
        LOG_E("LoRa FAILED: %s", r.GetErrorMessage());
        delay(5000);
        esp_restart();
    }

    g_mesh_ready = true;
    LOG_I("LoRa online addr=0x%04X", g_mesher->GetNodeAddress());
}

void mesh_send(AddressType dst, const std::vector<uint8_t>& payload) {
    if (!g_mesh_ready || !g_mesher) return;

    Result r = g_mesher->Send(dst, payload);
    if (!r) {
        LOG_W("Send failed (%s), queuing (size=%d)", r.GetErrorMessage(), (int)g_retry_queue.size());
        portENTER_CRITICAL(&g_queue_mux);
        if ((int)g_retry_queue.size() < 20) {  // MAX_RETRY_QUEUE
            g_retry_queue.push_back({dst, std::move(const_cast<std::vector<uint8_t>&>(payload)), 0});
        } else {
            LOG_E("Retry queue full, packet dropped");
        }
        portEXIT_CRITICAL(&g_queue_mux);
    }
}

void mesh_flush_retry_queue() {
    if (!g_mesh_ready) return;

    portENTER_CRITICAL(&g_queue_mux);
    if (g_retry_queue.empty()) { portEXIT_CRITICAL(&g_queue_mux); return; }
    PendingPacket pkt = g_retry_queue.front();
    g_retry_queue.pop_front();
    portEXIT_CRITICAL(&g_queue_mux);

    Result r = g_mesher->Send(pkt.dst, pkt.payload);
    if (!r) {
        pkt.retries++;
        if (pkt.retries < (uint8_t)MAX_RETRIES) {
            portENTER_CRITICAL(&g_queue_mux);
            g_retry_queue.push_front(std::move(pkt));
            portEXIT_CRITICAL(&g_queue_mux);
            LOG_D("Retry #%d failed, re-queued", pkt.retries);
        } else {
            LOG_E("Packet dropped after %d retries", MAX_RETRIES);
        }
    } else {
        LOG_D("Queued packet delivered to 0x%04X", pkt.dst);
    }
}

bool mesh_handle_ping(AddressType& pong_dst) {
    bool do_ping = false;
    portENTER_CRITICAL(&g_ping_mux);
    if (g_flag_ping_valid) {
        do_ping = true;
        pong_dst = g_ping_src;
        g_flag_ping_valid = false;
    }
    portEXIT_CRITICAL(&g_ping_mux);
    return do_ping;
}

void mesh_clear_ping() {
    // No-op: handled inside mesh_handle_ping
}

void mesh_send_announce(uint8_t node_type, uint8_t fw_ver) {
    if (!g_mesh_ready) return;
    Announce a;
    a.node_type = node_type;
    a.fw_ver = fw_ver;
    mesh_send(GATEWAY_LORA_ADDR,
              std::vector<uint8_t>((uint8_t*)&a, (uint8_t*)&a + sizeof(a)));
    LOG_I("ANNOUNCE sent (type=%d)", node_type);
}
