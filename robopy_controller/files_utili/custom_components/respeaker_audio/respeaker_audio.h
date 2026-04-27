#pragma once

#include "esphome/core/component.h"
#include "esphome/components/uart/uart.h"
#include "esphome/core/log.h"
#include <string>
#include <vector>

namespace esphome {
namespace respeaker {

static const char *const TAG = "respeaker_audio_streamer";

class AudioStreamerComponent : public Component {
 public:
  AudioStreamerComponent() = default;

  void set_uart(uart::UARTComponent *uart) { this->uart_ = uart; }

  void setup() override {
    ESP_LOGI(TAG, "AudioStreamerComponent initialized.");
    this->streaming_enabled_ = false;
    
    // ── TinyUSB Composite Device Init ────────────────────────────────────────
    // Se CONFIG_TINYUSB_DEVICE_COMPOSITE è abilitato in sdkconfig,
    // TinyUSB automaticamente expone CDC + Audio interfaces su una sola porta.
    // Macros verified at compile-time (no direct TinyUSB includes needed)
    #ifdef CONFIG_TINYUSB_DEVICE_COMPOSITE
      ESP_LOGI(TAG, "🔌 TinyUSB Composite Device ENABLED (CDC + Audio on single USB port)");
    #else
      ESP_LOGI(TAG, "⚠️  TinyUSB Composite Device NOT configured. Fallback: CDC-only");
    #endif
  }

  void set_streaming(bool enable) {
    this->streaming_enabled_ = enable;
    if (enable) {
        ESP_LOGI(TAG, "Audio streaming STARTED");
    } else {
        ESP_LOGI(TAG, "Audio streaming STOPPED");
    }
  }

  void process_audio_chunk(const int16_t *data, size_t num_samples) {
    if (!this->streaming_enabled_) return;
    if (num_samples == 0) return;
    
    // Converte int16_t in un array di bytes
    const uint8_t* byte_data = reinterpret_cast<const uint8_t*>(data);
    size_t in_len = num_samples * 2;
    
    std::string encoded = base64_encode(byte_data, in_len);
    
    this->uart_->write_str("AUDIO_PCM:");
    this->uart_->write_str(encoded.c_str());
    this->uart_->write_str("\n");
  }

 private:
  // Codificatore Base64 interno leggero e senza dipendenze per non litigare col compilatore
  std::string base64_encode(unsigned char const* bytes_to_encode, unsigned int in_len) {
    std::string ret;
    int i = 0;
    int j = 0;
    unsigned char char_array_3[3];
    unsigned char char_array_4[4];
    static const std::string base64_chars = 
                 "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "abcdefghijklmnopqrstuvwxyz"
                 "0123456789+/";

    while (in_len--) {
      char_array_3[i++] = *(bytes_to_encode++);
      if (i == 3) {
        char_array_4[0] = (char_array_3[0] & 0xfc) >> 2;
        char_array_4[1] = ((char_array_3[0] & 0x03) << 4) + ((char_array_3[1] & 0xf0) >> 4);
        char_array_4[2] = ((char_array_3[1] & 0x0f) << 2) + ((char_array_3[2] & 0xc0) >> 6);
        char_array_4[3] = char_array_3[2] & 0x3f;

        for(i = 0; (i <4) ; i++)
          ret += base64_chars[char_array_4[i]];
        i = 0;
      }
    }

    if (i) {
      for(j = i; j < 3; j++)
        char_array_3[j] = '\0';

      char_array_4[0] = (char_array_3[0] & 0xfc) >> 2;
      char_array_4[1] = ((char_array_3[0] & 0x03) << 4) + ((char_array_3[1] & 0xf0) >> 4);
      char_array_4[2] = ((char_array_3[1] & 0x0f) << 2) + ((char_array_3[2] & 0xc0) >> 6);
      char_array_4[3] = char_array_3[2] & 0x3f;

      for (j = 0; (j < i + 1); j++)
        ret += base64_chars[char_array_4[j]];

      while((i++ < 3))
        ret += '=';
    }
    return ret;
  }

 protected:
  uart::UARTComponent *uart_;
  bool streaming_enabled_;
};

}  // namespace respeaker
}  // namespace esphome
