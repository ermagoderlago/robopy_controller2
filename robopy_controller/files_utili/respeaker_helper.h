#pragma once
// respeaker_helper.h
// Includi qui tutte le intestazioni C++ necessarie per le lambda ESPHome.
#include "driver/usb_serial_jtag.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "esp_timer.h"          

// ── Ring Buffer Speaker ─────────────────────────────────────────────────
#define SPK_RB_SIZE 8192

struct SpeakerRingBuffer {
  uint8_t  buf[SPK_RB_SIZE];
  volatile size_t head;   // scrittore
  volatile size_t tail;   // lettore
  volatile size_t count;
} spk_rb = {0};

static portMUX_TYPE spk_mux = portMUX_INITIALIZER_UNLOCKED;

inline bool spk_rb_write(const uint8_t* data, size_t len) {
  taskENTER_CRITICAL(&spk_mux);
  if (spk_rb.count + len > SPK_RB_SIZE) {
    taskEXIT_CRITICAL(&spk_mux);
    return false;
  }
  for (size_t i = 0; i < len; i++) {
    spk_rb.buf[spk_rb.head] = data[i];
    spk_rb.head = (spk_rb.head + 1) % SPK_RB_SIZE;
  }
  spk_rb.count += len;
  taskEXIT_CRITICAL(&spk_mux);
  return true;
}

inline size_t spk_rb_read(uint8_t* out, size_t max_len) {
  taskENTER_CRITICAL(&spk_mux);
  size_t n = spk_rb.count < max_len ? spk_rb.count : max_len;
  for (size_t i = 0; i < n; i++) {
    out[i] = spk_rb.buf[spk_rb.tail];
    spk_rb.tail = (spk_rb.tail + 1) % SPK_RB_SIZE;
  }
  spk_rb.count -= n;
  taskEXIT_CRITICAL(&spk_mux);
  return n;
}