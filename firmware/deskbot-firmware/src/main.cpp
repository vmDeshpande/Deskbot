#include <Arduino.h>

#include "audio/audio_loop.h"
#include "network/device_websocket.h"
#include "network/time_sync.h"
#include "network/wifi_manager.h"
#include "log.h"

void setup() {
  Serial.begin(115200);

  // Boot banner
  Serial.println();
  Serial.println("========================");
  Serial.println("        DESKBOT");
  Serial.println("========================");
  Serial.println();

  LOG_INFO("BOOT", "ESP32 initialized");
  LOG_INFO("BOOT", "Chip: %s", ESP.getChipModel());
  LOG_INFO("BOOT", "CPU: %d MHz", ESP.getCpuFreqMHz());

  DeskbotWiFi::begin();
  DeskbotTime::begin();
  DeskbotWebSocket::begin();

  DeskbotAudio::audioLoopBegin();
}

void loop() {
  DeskbotWiFi::loop();
  DeskbotAudio::audioLoopLoop();
  yield();
}