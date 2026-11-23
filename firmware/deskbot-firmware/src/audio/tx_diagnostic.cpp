#include "tx_diagnostic.h"

#include <ESP_I2S.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr float kFrequency = 440.0f;
constexpr int16_t kAmplitude = 1000;
constexpr size_t kBufferFrames = 256;        // Frames per TX write (matches speaker_test)
constexpr size_t kRxDrainFrames = 512;       // Frames per RX drain (keeps RX DMA queue empty)
constexpr uint32_t kReportIntervalMs = 500;  // Verbose per-write reporting cadence

I2SClass i2s;
bool i2s_initialized = false;
float phase = 0.0f;
const float phase_increment = 2.0f * PI * kFrequency / kSampleRate;

// TX buffer: stereo 32-bit frames. Even indices = left slot, odd = right slot.
uint32_t tx_buffer[kBufferFrames * 2];
// RX drain buffer: stereo 32-bit frames.
uint32_t rx_buffer[kRxDrainFrames * 2];

size_t writes_total = 0;
size_t writes_partial = 0;
size_t writes_error = 0;
size_t rx_bytes_drained = 0;
uint32_t last_report_ms = 0;

}  // namespace

void txDiagnosticBegin() {
  if (i2s_initialized) return;

  i2s.setPins(26, 25, 22, 33);

  Serial.println("[TXDIAG] Initializing I2S: Standard Philips, 16 kHz, 32-bit, stereo, RX+TX together");
  if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO, -1)) {
    Serial.printf("[TXDIAG] ERROR: I2S begin() failed! lastError=%d\n", i2s.lastError());
    return;
  }

  // Both RX and TX stay enabled after begin(). Confirm the configuration is
  // exactly what we asked for (rate, width, slot mode on the TX channel).
  Serial.printf("[TXDIAG] After begin: TX rate=%lu bits=%d mode=%d | RX rate=%lu bits=%d mode=%d\n",
                (unsigned long)i2s.txSampleRate(), (int)i2s.txDataWidth(), (int)i2s.txSlotMode(),
                (unsigned long)i2s.rxSampleRate(), (int)i2s.rxDataWidth(), (int)i2s.rxSlotMode());

  i2s_initialized = true;
  phase = 0.0f;
  writes_total = 0;
  writes_partial = 0;
  writes_error = 0;
  rx_bytes_drained = 0;
  last_report_ms = millis();

  Serial.println("[TXDIAG] TX diagnostic started: 440 Hz sine, amplitude=1000, 32-bit stereo, MSB-aligned in bits 31-16");
}

void txDiagnosticLoop() {
  if (!i2s_initialized) return;

  // Build kBufferFrames frames of 440 Hz sine. Each frame has a left and right
  // slot; both slots receive the identical signed sample placed in bits 31-16.
  for (size_t i = 0; i < kBufferFrames; ++i) {
    int16_t sample = static_cast<int16_t>(kAmplitude * sinf(phase));
    phase += phase_increment;
    if (phase >= 2.0f * PI) phase -= 2.0f * PI;

    // Signed 16-bit sample -> bits 31-16 of a 32-bit slot (MSB aligned).
    uint32_t slot = static_cast<uint32_t>(static_cast<int32_t>(sample) << 16);
    tx_buffer[i * 2] = slot;      // Left slot
    tx_buffer[i * 2 + 1] = slot;  // Right slot
  }

  size_t requested = kBufferFrames * 2 * sizeof(uint32_t);
  size_t actual = i2s.write(tx_buffer, requested);

  // Report every TX write with the requested fields.
  int err = i2s.lastError();
  if (actual != requested) {
    writes_partial++;
  }
  if (err != ESP_OK) {
    writes_error++;
  }
  writes_total++;

  uint32_t now = millis();
  if (now - last_report_ms >= kReportIntervalMs || actual != requested || err != ESP_OK) {
    Serial.printf("[TXDIAG] TX write #%u: requested=%u actual=%u lastError=%d (%s) | TX rate=%lu bits=%d mode=%d\n",
                  (unsigned)writes_total, (unsigned)requested, (unsigned)actual, err,
                  esp_err_to_name((esp_err_t)err), (unsigned long)i2s.txSampleRate(),
                  (int)i2s.txDataWidth(), (int)i2s.txSlotMode());
    last_report_ms = now;
  }

  // Drain RX in appropriately sized chunks so the RX DMA queue does not fill.
  // kRxDrainFrames (512) is a full drain, not a 4-frame peek.
  size_t rx_actual = i2s.readBytes((char *)rx_buffer, sizeof(rx_buffer));
  rx_bytes_drained += rx_actual;

  // Periodic summary.
  if (now - last_report_ms >= kReportIntervalMs) {
    Serial.printf("[TXDIAG] Summary: writes=%u partial=%u errors=%u rx_drained_bytes=%u heap=%lu\n",
                  (unsigned)writes_total, (unsigned)writes_partial, (unsigned)writes_error,
                  (unsigned)rx_bytes_drained, (unsigned long)ESP.getFreeHeap());
    last_report_ms = now;
  }
}

void txDiagnosticEnd() {
  if (!i2s_initialized) return;

  i2s.end();
  i2s_initialized = false;

  Serial.printf("[TXDIAG] Stopped: writes=%u partial=%u errors=%u rx_drained_bytes=%u\n",
                (unsigned)writes_total, (unsigned)writes_partial, (unsigned)writes_error,
                (unsigned)rx_bytes_drained);
}

}  // namespace DeskbotAudio