#ifndef UAC2_BRIDGE_H
#define UAC2_BRIDGE_H

#include "esphome.h"
#include "esp_log.h"
#include "tusb.h"
#include "tusb_cdc_acm.h"
#include "tusb_audio.h"

static const char *const TAG_USB = "uac2_bridge";

class UAC2Bridge : public esphome::Component {
 public:
  void setup() override {
    ESP_LOGI(TAG_USB, "Initializing USB UAC2...");
  }
};

#endif
