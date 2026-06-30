// log.hpp — Logging macros for all firmware types.
// Level: 0=off, 1=error, 2=warn, 3=info, 4=debug
#pragma once

#ifndef LOG_LEVEL
  #define LOG_LEVEL 3
#endif

#define LOG_E(fmt, ...) do { if (LOG_LEVEL >= 1) { Serial.printf("[ERR] " fmt "\n", ##__VA_ARGS__); } } while (0)
#define LOG_W(fmt, ...) do { if (LOG_LEVEL >= 2) { Serial.printf("[WRN] " fmt "\n", ##__VA_ARGS__); } } while (0)
#define LOG_I(fmt, ...) do { if (LOG_LEVEL >= 3) { Serial.printf("[INF] " fmt "\n", ##__VA_ARGS__); } } while (0)
#define LOG_D(fmt, ...) do { if (LOG_LEVEL >= 4) { Serial.printf("[DBG] " fmt "\n", ##__VA_ARGS__); } } while (0)
