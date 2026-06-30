// blink.hpp — LED blink utility.
// Blink must be called from main loop, NOT from ISR/callback context.
// Use blink_request_from_isr() to set flag, dispatch in loop().
#pragma once

#include <Arduino.h>

#ifndef PIN_LED
  #define PIN_LED 35
#endif

inline void blink_init() {
    pinMode(PIN_LED, OUTPUT);
}

inline void blink(uint8_t times, uint16_t ms) {
    for (uint8_t i = 0; i < times; i++) {
        digitalWrite(PIN_LED, HIGH);
        vTaskDelay(pdMS_TO_TICKS(ms));
        digitalWrite(PIN_LED, LOW);
        if (i < times - 1) vTaskDelay(pdMS_TO_TICKS(ms));
    }
}
