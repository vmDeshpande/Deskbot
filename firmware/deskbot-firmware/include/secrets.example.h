#pragma once

#include <stdint.h>

// Copy this file to secrets.h and replace these placeholders with local values.
// Keep secrets.h private; it is excluded from Git.
static constexpr char WIFI_SSID[] = "YOUR_WIFI_SSID";
static constexpr char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";

// Development backend connection. Replace every placeholder in your ignored secrets.h.
static constexpr char DESKBOT_WS_HOST[] = "WINDOWS_LAN_IP";
static constexpr uint16_t DESKBOT_WS_PORT = 8000;
static constexpr char DESKBOT_WS_PATH[] = "/ws/device";
static constexpr char DESKBOT_DEVICE_ID[] = "REPLACE_WITH_PROVISIONED_UUID_V4";
static constexpr char DESKBOT_DEVICE_TOKEN[] = "REPLACE_WITH_64_HEX_CHARACTER_TOKEN";
