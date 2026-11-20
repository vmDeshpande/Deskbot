
#include <Arduino.h>
#include <WiFi.h>
#include "secrets.h"

void connectWiFi()
{
  Serial.println();
  Serial.println("[WIFI] Connecting...");

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;

  while (WiFi.status() != WL_CONNECTED && attempts < 30)
  {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  Serial.println();

  if (WiFi.status() == WL_CONNECTED)
  {
    Serial.println("[WIFI] Connected!");
    // Serial.print("[WIFI] MAC Address: ");
    // Serial.println(WiFi.macAddress());

    Serial.print("[WIFI] IP Address: ");
    Serial.println(WiFi.localIP());

    Serial.print("[WIFI] Signal Strength: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
  }
  else
  {
    Serial.println("[WIFI] Connection failed!");
    Serial.println("[WIFI] Check SSID, password and 2.4GHz network.");
  }
}

void setup()
{
  Serial.begin(115200);
  delay(1000);

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

  connectWiFi();
}

void loop()
{
  static unsigned long lastHeartbeat = 0;

  if (millis() - lastHeartbeat >= 5000)
  {
    lastHeartbeat = millis();

    if (WiFi.status() == WL_CONNECTED)
    {
      Serial.println("[DESKBOT] Online | WiFi connected");
    }
    else
    {
      Serial.println("[DESKBOT] WiFi disconnected");
    }
  }
}
