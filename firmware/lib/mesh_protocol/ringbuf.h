// ringbuf.h — UART Ring Buffer (ISR-safe)
// Thread-safe ring buffer for UART RX. ISR writes bytes, main loop reads lines.
#pragma once

#include <cstddef>
#include <cstdint>
#include "mesh_types.h"

struct RingBuf {
    uint8_t data[UART_RX_BUF_SIZE];
    volatile size_t head = 0;  // ISR writes here
    volatile size_t tail = 0;  // main reads from here

    // Push one byte — safe to call from ISR
    void push(uint8_t byte) {
        size_t next = (head + 1) % UART_RX_BUF_SIZE;
        if (next != tail) {         // not full
            data[head] = byte;
            head = next;
        }
        // else: drop byte (buffer full) — caller should track drops
    }

    // How many bytes available to read
    size_t available() const {
        if (head >= tail) return head - tail;
        return UART_RX_BUF_SIZE - tail + head;
    }

    // Pop one byte — call from main loop only
    uint8_t pop() {
        uint8_t byte = data[tail];
        if (head != tail) {
            tail = (tail + 1) % UART_RX_BUF_SIZE;
        }
        return byte;
    }

    // Read a complete line (up to '\n'). Returns length (excluding '\n'),
    // or -1 if no complete line available. max_len includes null terminator.
    int read_line(char* out, size_t max_len) {
        size_t avail = available();
        if (avail == 0) return -1;

        // Scan for '\n' in buffer
        size_t pos = 0;
        bool found = false;
        for (size_t i = 0; i < avail && i < UART_RX_BUF_SIZE; i++) {
            size_t idx = (tail + i) % UART_RX_BUF_SIZE;
            if (data[idx] == '\n') {
                pos = i;
                found = true;
                break;
            }
        }
        if (!found) return -1;

        // Copy line to output (excluding '\n')
        size_t copy_len = (pos < max_len - 1) ? pos : max_len - 1;
        for (size_t i = 0; i < copy_len; i++) {
            out[i] = (char)data[(tail + i) % UART_RX_BUF_SIZE];
        }
        out[copy_len] = '\0';

        // Advance tail past '\n'
        tail = (tail + pos + 1) % UART_RX_BUF_SIZE;

        // Strip '\r' if present at end
        if (copy_len > 0 && out[copy_len - 1] == '\r') {
            out[copy_len - 1] = '\0';
            return (int)(copy_len - 1);
        }
        return (int)copy_len;
    }
};
