// watchdog.hpp — Watchdog Timer abstraction.
// Uses ESP32 Task Watchdog Timer (TWDT) for 30s hardware reset.
#pragma once

#include "esp_task_wdt.h"

#ifndef WDT_TIMEOUT_S
  #define WDT_TIMEOUT_S 30
#endif

inline void watchdog_init() {
    esp_task_wdt_init(WDT_TIMEOUT_S, true);
    esp_task_wdt_add(NULL);
    LOG_I("WDT enabled (%ds)", WDT_TIMEOUT_S);
}

inline void watchdog_reset() {
    esp_task_wdt_reset();
}
