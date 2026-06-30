// node_table.hpp — LoRa Gateway node routing table (max 20 nodes).
#pragma once

#include <cstdint>
#include "mesh_types.h"
#include "loramesher.hpp"

using namespace loramesher;

#define MAX_NODES 20

struct NodeEntry {
    AddressType lora_addr;
    uint8_t     node_id;
    uint8_t     node_type;
    bool        active;
};

int  table_find_or_add(AddressType addr, uint8_t ntype,
                       void (*on_table_full)(AddressType));
int  table_find_by_id(uint8_t id);
int  table_get_count();
void table_set_node_id(int idx, uint8_t id);
extern NodeEntry g_node_table[MAX_NODES];
extern uint8_t   g_node_count;
