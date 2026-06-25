// ringbuf.h — UART Ring Buffer (ISR-safe)
// Thread-safe ring buffer for UART RX. ISR writes bytes, main loop reads lines.
//
// CHANGES vs original:
//   - pop() now returns false on empty instead of returning garbage
//   - read_line() snapshots head once before scan → safe against concurrent ISR
//   - drop_count tracks bytes lost due to buffer-full condition
//   - available() made const-correct with snapshot approach
#pragma once

#include <cstddef>
#include <cstdint>
#include "mesh_types.h"

struct RingBuf {
    uint8_t          data[UART_RX_BUF_SIZE];
    volatile size_t  head       = 0;   // ISR writes here
    volatile size_t  tail       = 0;   // main loop reads here
    volatile uint32_t drop_count = 0;  // bytes dropped due to full buffer

    // Push one byte — safe to call from ISR.
    void push(uint8_t byte) {
        size_t next = (head + 1) % UART_RX_BUF_SIZE;
        if (next != tail) {
            data[head] = byte;
            head = next;
        } else {
            drop_count++;   // buffer full — track loss, caller can log
        }
    }

    // How many bytes are available to read.
    // Snapshots head once so the value is stable even if ISR writes during call.
    size_t available() const {
        size_t h = head;   // single read of volatile
        size_t t = tail;
        if (h >= t) return h - t;
        return UART_RX_BUF_SIZE - t + h;
    }

    // Pop one byte. Returns true on success, false if buffer is empty.
    // Call from main loop only.
    bool pop(uint8_t& out) {
        if (head == tail) return false;
        out  = data[tail];
        tail = (tail + 1) % UART_RX_BUF_SIZE;
        return true;
    }

    // Read a complete '\n'-terminated line into out[0..max_len-1].
    // Returns line length (excluding '\n'), or -1 if no complete line yet.
    // Strips trailing '\r' if present.
    //
    // ISR-safety: snapshots head at entry so the scan operates on a fixed
    // window. Bytes arriving after the snapshot are deferred to the next call.
    int read_line(char* out, size_t max_len) {
        if (max_len == 0) return -1;

        // Snapshot: treat only bytes visible right now
        size_t h = head;
        size_t t = tail;

        size_t avail = (h >= t) ? (h - t) : (UART_RX_BUF_SIZE - t + h);
        if (avail == 0) return -1;

        // Scan for '\n' within the snapshot window
        size_t pos   = 0;
        bool   found = false;
        for (size_t i = 0; i < avail; i++) {
            if (data[(t + i) % UART_RX_BUF_SIZE] == '\n') {
                pos   = i;
                found = true;
                break;
            }
        }
        if (!found) return -1;

        // Copy bytes before '\n', honouring max_len
        size_t copy_len = (pos < max_len - 1) ? pos : max_len - 1;
        for (size_t i = 0; i < copy_len; i++) {
            out[i] = (char)data[(t + i) % UART_RX_BUF_SIZE];
        }
        out[copy_len] = '\0';

        // Advance tail past '\n'
        tail = (t + pos + 1) % UART_RX_BUF_SIZE;

        // Strip '\r'
        if (copy_len > 0 && out[copy_len - 1] == '\r') {
            out[--copy_len] = '\0';
        }
        return (int)copy_len;
    }
};