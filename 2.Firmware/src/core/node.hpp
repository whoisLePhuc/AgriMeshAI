// node.hpp — Node identity management (NVS persistence).
#pragma once

#include <Arduino.h>
#include <Preferences.h>
#include "mesh_types.h"
#include "core/mesh.hpp"
#include "common/log.hpp"

// Load node address from NVS, or save the live address if first boot.
inline AddressType node_init(AddressType live_addr) {
    Preferences prefs;
    prefs.begin("mesh", false);
    uint16_t saved = prefs.getUShort("addr", 0);
    if (saved == 0) {
        prefs.putUShort("addr", (uint16_t)live_addr);
        saved = (uint16_t)live_addr;
        LOG_I("NVS: new addr saved 0x%04X", saved);
    } else if (saved != (uint16_t)live_addr) {
        LOG_W("NVS addr 0x%04X != live 0x%04X, using NVS", saved, (uint16_t)live_addr);
    }
    prefs.end();
    return (AddressType)saved;
}
