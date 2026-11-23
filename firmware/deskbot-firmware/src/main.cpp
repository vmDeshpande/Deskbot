#include <Arduino.h>

#include "network/device_websocket.h"
#include "network/time_sync.h"
#include "network/wifi_manager.h"

#ifdef DESKBOT_AUDIO_SPEAKER_TEST
#include "audio/speaker_test.h"
#endif

#ifdef DESKBOT_AUDIO_MIC_TEST
#include "audio/mic_test.h"
#endif

#ifdef DESKBOT_AUDIO_LOOPBACK_TEST
#include "audio/loopback_test.h"
#endif

#ifdef DESKBOT_AUDIO_TX_DIAGNOSTIC
#include "audio/tx_diagnostic.h"
#endif

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

#ifdef DESKBOT_AUDIO_SPEAKER_TEST
  DeskbotAudio::speakerTestBegin();
#endif

#ifdef DESKBOT_AUDIO_MIC_TEST
  DeskbotAudio::micTestBegin();
#endif

#ifdef DESKBOT_AUDIO_LOOPBACK_TEST
  DeskbotAudio::loopbackTestBegin();
#endif

#ifdef DESKBOT_AUDIO_TX_DIAGNOSTIC
  DeskbotAudio::txDiagnosticBegin();
#endif
}

void loop() {
  DeskbotWiFi::loop();

#ifdef DESKBOT_AUDIO_SPEAKER_TEST
  DeskbotAudio::speakerTestLoop();
#endif

#ifdef DESKBOT_AUDIO_MIC_TEST
  DeskbotAudio::micTestLoop();
#endif

#ifdef DESKBOT_AUDIO_LOOPBACK_TEST
  DeskbotAudio::loopbackTestLoop();
#endif

#ifdef DESKBOT_AUDIO_TX_DIAGNOSTIC
  DeskbotAudio::txDiagnosticLoop();
#endif

  yield();
}
