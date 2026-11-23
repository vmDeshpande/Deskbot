#include "mic_test.h"

#include <ESP_I2S.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kReadFrames = 512;
constexpr uint32_t kReportIntervalMs = 1000;

I2SClass i2s;
bool i2s_initialized = false;
uint32_t last_report_ms = 0;

int32_t samples_captured = 0;
int64_t left_sum_squares = 0;
int64_t right_sum_squares = 0;
int32_t left_peak = 0;
int32_t right_peak = 0;
int32_t left_min = INT32_MAX;
int32_t left_max = INT32_MIN;
int32_t right_min = INT32_MAX;
int32_t right_max = INT32_MIN;

uint32_t raw32_buffer[kReadFrames];

}  // namespace

void micTestBegin() {
  if (i2s_initialized) return;

  // INMP441 pins: SCK=GPIO26, WS=GPIO25, SD=GPIO33
  // Configure RX only (DOUT=-1). Do NOT initialize TX.
  i2s.setPins(26, 25, -1, 33);

  // Initialize I2S in Standard Philips mode, RX only
  // INMP441 outputs 24-bit samples in 32-bit slots (MSB aligned), stereo frame (64 BCLK/frame)
  // Use stereo slot mode to get correct clock timing for INMP441
  // slot_mask = -1 (default) -> ESP_I2S will use I2S_STD_SLOT_BOTH for stereo
  if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO, -1)) {
    Serial.println("[AUDIO] Failed to initialize I2S for microphone test!");
    return;
  }

  // No transform - read raw 32-bit stereo samples
  if (!i2s.configureRX(kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO,
                       I2S_RX_TRANSFORM_NONE, -1)) {
    Serial.println("[AUDIO] Failed to configure RX!");
    i2s.end();
    return;
  }

  i2s_initialized = true;
  last_report_ms = millis();
  Serial.println("[AUDIO] Mic diag started: 16 kHz, 32-bit stereo, raw 32-bit capture");
}

void micTestLoop() {
  if (!i2s_initialized) return;

  // Read raw 32-bit stereo samples (2 slots per frame)
  // Each frame = 2 x 32-bit = 8 bytes
  size_t bytes_read = i2s.readBytes((char *)raw32_buffer, sizeof(raw32_buffer));
  size_t samples32 = bytes_read / sizeof(uint32_t);

  if (samples32 == 0) return;

  // In stereo mode, samples alternate: left, right, left, right...
  // Even indices = left slot (WS low), Odd indices = right slot (WS high)
  // INMP441 L/R=GND -> left slot active
  size_t left_count = 0;
  size_t right_count = 0;

  for (size_t i = 0; i < samples32; ++i) {
    uint32_t raw = raw32_buffer[i];
    int32_t sample = static_cast<int32_t>(raw);  // Sign-extend for 24-bit MSB-aligned

    if ((i & 1) == 0) {
      // Left slot (WS low)
      left_count++;
      left_sum_squares += (int64_t)sample * sample;
      if (sample > left_peak) left_peak = sample;
      if (-sample > left_peak) left_peak = -sample;
      if (sample < left_min) left_min = sample;
      if (sample > left_max) left_max = sample;
    } else {
      // Right slot (WS high)
      right_count++;
      right_sum_squares += (int64_t)sample * sample;
      if (sample > right_peak) right_peak = sample;
      if (-sample > right_peak) right_peak = -sample;
      if (sample < right_min) right_min = sample;
      if (sample > right_max) right_max = sample;
    }
  }

  samples_captured += samples32;

  uint32_t now = millis();
  if (now - last_report_ms >= kReportIntervalMs) {
    float left_rms = 0.0f, right_rms = 0.0f;
    if (left_count > 0) left_rms = sqrtf((float)left_sum_squares / left_count);
    if (right_count > 0) right_rms = sqrtf((float)right_sum_squares / right_count);

    // Print first 8 raw 32-bit samples in hex
    Serial.print("[AUDIO] raw32[0..7]:");
    size_t print_count = (samples32 < 8) ? samples32 : 8;
    for (size_t i = 0; i < print_count; ++i) {
      Serial.printf(" 0x%08lX", (unsigned long)raw32_buffer[i]);
    }
    Serial.println();

    Serial.printf(
        "[AUDIO] total_32bit=%lu left=frames=%lu rms=%.1f peak=%ld min=%ld max=%ld | "
        "right=frames=%lu rms=%.1f peak=%ld min=%ld max=%ld\n",
        (unsigned long)samples32,
        (unsigned long)left_count, left_rms, (long)left_peak, (long)left_min, (long)left_max,
        (unsigned long)right_count, right_rms, (long)right_peak, (long)right_min, (long)right_max);

    samples_captured = 0;
    left_sum_squares = 0;
    right_sum_squares = 0;
    left_peak = 0;
    right_peak = 0;
    left_min = INT32_MAX;
    left_max = INT32_MIN;
    right_min = INT32_MAX;
    right_max = INT32_MIN;
    last_report_ms = now;
  }
}

void micTestEnd() {
  if (!i2s_initialized) return;
  i2s.end();
  i2s_initialized = false;
  Serial.println("[AUDIO] Microphone test stopped");
}

}  // namespace DeskbotAudio