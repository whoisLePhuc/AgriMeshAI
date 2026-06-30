// node_table.cpp
#include "gateway/node_table.hpp"
#include <cstdio>

NodeEntry g_node_table[MAX_NODES];
uint8_t   g_node_count = 0;

int table_find_or_add(AddressType addr, uint8_t ntype,
                       void (*on_table_full)(AddressType)) {
    for (uint8_t i = 0; i < g_node_count; i++) {
        if (g_node_table[i].lora_addr == addr) return i;
    }
    if (g_node_count >= MAX_NODES) {
        if (on_table_full) on_table_full(addr);
        return -1;
    }
    g_node_table[g_node_count] = {addr, 0, ntype, true};
    return g_node_count++;
}

int table_find_by_id(uint8_t id) {
    for (uint8_t i = 0; i < g_node_count; i++) {
        if (g_node_table[i].node_id == id && g_node_table[i].active) return i;
    }
    return -1;
}

int table_get_count() { return g_node_count; }

void table_set_node_id(int idx, uint8_t id) {
    if (idx >= 0 && idx < (int)g_node_count) g_node_table[idx].node_id = id;
}
