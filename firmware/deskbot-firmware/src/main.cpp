#include <Arduino.h>

#include "audio/audio_loop.h"
#include "network/device_websocket.h"
#include "network/time_sync.h"
#include "network/wifi_manager.h"

void setup() {
  Serial.begin(115200);

  Serial.println();
  Serial.println("==========================");
  Serial.println("       DESKBOT");
  Serial.println("    SYSTEM STARTING");
  Serial.println("==========================");
  Serial.print("[SYSTEM] Chip: ");
  Serial.println(ESP.getChipModel());
  Serial.print("[SYSTEM] CPU: ");
  Serial.print(ESP.getCpuFreqMHz());
  Serial.println(" MHz");

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
