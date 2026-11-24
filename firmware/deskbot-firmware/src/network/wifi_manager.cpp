#include "wifi_manager.h"

#include <WiFi.h>
#include "secrets.h"
#include "log.h"

namespace DeskbotWiFi {
namespace {

bool previousConnected = false;
bool started = false;

}  // namespace

void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  LOG_INFO("WIFI", "Connecting...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  started = true;
}

void loop() {
  if (!started) return;
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected == previousConnected) return;
  previousConnected = connected;

  if (connected) {
    LOG_INFO("WIFI", "Connected | %s", WiFi.localIP().toString().c_str());
    LOG_DEBUG("WIFI", "Signal: %d dBm", WiFi.RSSI());
  } else {
    LOG_WARN("WIFI", "Disconnected; reconnecting...");
  }
}

bool isConnected() { return WiFi.status() == WL_CONNECTED; }

}  // namespace DeskbotWiFi