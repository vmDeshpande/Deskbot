#pragma once

#include <Arduino.h>

namespace DeskbotWebSocket {

void begin();
void loop(bool wifiConnected, bool clockSynchronized);
bool isRegistered();

}  // namespace DeskbotWebSocket
