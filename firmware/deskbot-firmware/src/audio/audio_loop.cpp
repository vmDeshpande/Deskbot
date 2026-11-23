#include "audio_loop.h"

#include <ESP_I2S.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kReadFrames = 256;       // I2S frames per read/write block (~16 ms @ 16 kHz)
constexpr uint32_t kReportIntervalMs = 2000;
constexpr float kOutputGain = 0.5f;       // conservative starting gain (-6 dB)

I2SClass i2s;
bool i2s_initialized = false;
uint32_t last_report_ms = 0;

uint32_t rx_raw32[kReadFrames * 2];       // stereo 32-bit RX read buffer (2 slots/frame)
int16_t tx_pcm16[kReadFrames];             // mono PCM16 processing buffer
uint32_t tx_raw32[kReadFrames * 2];        // stereo 32-bit TX write buffer

size_t blocks_processed = 0;
size_t samples_processed = 0;

}  // namespace

void audioLoopBegin() {
    if (i2s_initialized) return;

    // Pins: BCLK GPIO26, WS GPIO25, speaker DIN GPIO22, mic SD GPIO33.
    i2s.setPins(26, 25, 22, 33);

    Serial.println("[AUDIO] Initializing I2S...");
    if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO, -1)) {
        Serial.printf("[AUDIO] ERROR: I2S begin() failed! lastError=%d\n", i2s.lastError());
        return;
    }

    if (!i2s.configureRX(kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO,
                         I2S_RX_TRANSFORM_NONE, -1)) {
        Serial.printf("[AUDIO] ERROR: configureRX failed! lastError=%d\n", i2s.lastError());
        i2s.end();
        return;
    }

    // Both RX and TX remain ENABLED for the entire run. Clocks stay running.
    // Never disable RX during playback or TX during capture: on ESP32 HW v1
    // full-duplex STD mode, RX shares TX's BCLK/WS (sig_loopback), so disabling
    // either channel disrupts the shared clock and breaks the other.
    Serial.printf("[AUDIO] RX/TX initialized: %u Hz, 32-bit stereo\n",
                  (unsigned int)kSampleRate);
    Serial.println("[AUDIO] Continuous microphone-to-speaker loopback active");

    i2s_initialized = true;
    last_report_ms = millis();
}

void audioLoopLoop() {
    if (!i2s_initialized) return;

    // --- Read microphone (RX) ---
    // Blocking read with driver timeout; returns available data.
    size_t bytes_read = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
    size_t samples32 = bytes_read / sizeof(uint32_t);
    size_t frames = samples32 / 2;

    if (frames == 0) {
        int err = i2s.lastError();
        if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
            Serial.printf("[AUDIO] RX read error: %d\n", err);
        }
        return;
    }

    // --- Convert INMP441 24-bit MSB-aligned (left slot) to PCM16 ---
    size_t sample_count = 0;
    for (size_t i = 0; i < frames; ++i) {
        // Left slot (WS low). INMP441 L/R=GND -> data on left channel only.
        uint32_t raw = rx_raw32[i * 2];
        // 24-bit MSB-aligned in 32-bit slot: sign-extend from bit 23.
        int32_t sample_24 = static_cast<int32_t>(raw) >> 8;
        // Top 16 bits -> PCM16.
        int16_t sample_16 = static_cast<int16_t>(sample_24 >> 8);
        // Apply gain with clamping to prevent overflow/clipping.
        int32_t gained = static_cast<int32_t>(sample_16) * kOutputGain;
        if (gained > 32767) gained = 32767;
        if (gained < -32768) gained = -32768;
        tx_pcm16[sample_count++] = static_cast<int16_t>(gained);
    }

    // --- Convert PCM16 to 32-bit MSB-aligned stereo I²S TX format ---
    for (size_t i = 0; i < sample_count; ++i) {
        int32_t sample_32 = static_cast<int32_t>(tx_pcm16[i]) * 65536;
        tx_raw32[i * 2]     = static_cast<uint32_t>(sample_32);     // left slot
        tx_raw32[i * 2 + 1] = static_cast<uint32_t>(sample_32);     // right slot
    }

    // --- Write to speaker (TX) ---
    size_t bytes_to_write = sample_count * 2 * sizeof(uint32_t);
    size_t bytes_written = i2s.write(tx_raw32, bytes_to_write);
    if (bytes_written != bytes_to_write) {
        Serial.printf("[AUDIO] TX write incomplete: %u/%u bytes\n",
                      (unsigned int)bytes_written, (unsigned int)bytes_to_write);
    }

    blocks_processed++;
    samples_processed += sample_count;

    // --- Periodic status log (concise, not per-block) ---
    uint32_t now = millis();
    if (now - last_report_ms >= kReportIntervalMs) {
        uint32_t free_heap = ESP.getFreeHeap();
        Serial.printf("[AUDIO] blocks=%lu samples=%lu heap=%lu\n",
                      (unsigned long)blocks_processed,
                      (unsigned long)samples_processed,
                      (unsigned long)free_heap);
        last_report_ms = now;
    }
}

void audioLoopEnd() {
    if (!i2s_initialized) return;
    i2s.end();
    i2s_initialized = false;
}

}  // namespace DeskbotAudio
