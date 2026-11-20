#pragma once

#include <Arduino.h>

namespace DeskbotTime {

void begin();
void loop(bool wifiConnected);
bool isSynchronized();

}  // namespace DeskbotTime
