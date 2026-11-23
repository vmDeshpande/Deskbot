#pragma once

#include <Arduino.h>

namespace DeskbotAudio {

void txDiagnosticBegin();
void txDiagnosticLoop();
void txDiagnosticEnd();

}  // namespace DeskbotAudio