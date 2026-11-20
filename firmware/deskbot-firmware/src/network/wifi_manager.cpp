#include "wifi_manager.h"

#include <WiFi.h>
#include "secrets.h"

namespace DeskbotWiFi {
namespace {

bool previousConnected = false;
bool started = false;

}  // namespace

void begin() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  Serial.println("[WIFI] Connecting asynchronously...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  started = true;
}

void loop() {
  if (!started) return;
  const bool connected = WiFi.status() == WL_CONNECTED;
  if (connected == previousConnected) return;
  previousConnected = connected;

  if (connected) {
    Serial.println("[WIFI] Connected.");
    Serial.print("[WIFI] IP Address: ");
    Serial.println(WiFi.localIP());
    Serial.print("[WIFI] Signal Strength: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
  } else {
    Serial.println("[WIFI] Disconnected; waiting for Wi-Fi auto-reconnect.");
  }
}

bool isConnected() { return WiFi.status() == WL_CONNECTED; }

}  // namespace DeskbotWiFi
