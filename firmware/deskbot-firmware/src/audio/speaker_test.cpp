#include "speaker_test.h"

#include <ESP_I2S.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr float kFrequency = 440.0f;
constexpr int16_t kAmplitude = 1000;
constexpr size_t kBufferFrames = 256;

I2SClass i2s;
bool i2s_initialized = false;
float phase = 0.0f;
const float phase_increment = 2.0f * PI * kFrequency / kSampleRate;

int16_t tx_buffer[kBufferFrames];

}  // namespace

void speakerTestBegin() {
  if (i2s_initialized) return;

  i2s.setPins(26, 25, 22);

  if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    Serial.println("[AUDIO] Failed to initialize I2S for speaker test!");
    return;
  }

  i2s_initialized = true;
  phase = 0.0f;
  Serial.println("[AUDIO] Speaker test started: 440 Hz sine @ 16 kHz, 16-bit mono, amplitude=1000");
}

void speakerTestLoop() {
  if (!i2s_initialized) return;

  for (size_t i = 0; i < kBufferFrames; ++i) {
    tx_buffer[i] = static_cast<int16_t>(kAmplitude * sinf(phase));
    phase += phase_increment;
    if (phase >= 2.0f * PI) phase -= 2.0f * PI;
  }

  size_t bytes_written = i2s.write(tx_buffer, sizeof(tx_buffer));
  if (bytes_written != sizeof(tx_buffer)) {
    Serial.printf("[AUDIO] I2S write incomplete: %u/%u bytes\n", (unsigned)bytes_written, (unsigned)sizeof(tx_buffer));
  }
}

void speakerTestEnd() {
  if (!i2s_initialized) return;
  i2s.end();
  i2s_initialized = false;
  Serial.println("[AUDIO] Speaker test stopped");
}

}  // namespace DeskbotAudio