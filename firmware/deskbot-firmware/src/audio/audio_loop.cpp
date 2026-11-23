#include "audio_loop.h"

#include <ESP_I2S.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kReadFrames = 256;       // I2S frames per read/write block (~16 ms @ 16 kHz)
constexpr size_t kCaptureSamples = 16000;  // 1 second of mono PCM16 @ 16 kHz
constexpr uint32_t kCooldownMs = 500;      // 500 ms cooldown after playback
constexpr uint32_t kReportIntervalMs = 2000;
constexpr float kOutputGain = 0.5f;        // conservative starting gain (-6 dB)

enum class AudioState {
    CAPTURING,
    PLAYBACK,
    COOLDOWN
};

I2SClass i2s;
bool i2s_initialized = false;
AudioState current_state = AudioState::CAPTURING;
uint32_t last_report_ms = 0;
uint32_t cooldown_start_ms = 0;

uint32_t rx_raw32[kReadFrames * 2];       // stereo 32-bit RX read buffer (2 slots/frame)
int16_t recording_buffer[kCaptureSamples]; // bounded mono PCM16 recording buffer (32 KB)
uint32_t tx_silence32[kReadFrames * 2];   // silence for TX during CAPTURING/COOLDOWN
uint32_t tx_raw32[kReadFrames * 2];        // stereo 32-bit TX write buffer

size_t samples_recorded = 0;   // samples captured in recording_buffer
size_t samples_played = 0;     // samples played from recording_buffer

const char *stateName(AudioState s) {
    switch (s) {
        case AudioState::CAPTURING: return "CAPTURING";
        case AudioState::PLAYBACK: return "PLAYBACK";
        case AudioState::COOLDOWN: return "COOLDOWN";
    }
    return "UNKNOWN";
}

void transitionTo(AudioState new_state) {
    if (current_state == new_state) return;
    current_state = new_state;
    Serial.printf("[AUDIO] State: %s\n", stateName(new_state));
}

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

    memset(tx_silence32, 0, sizeof(tx_silence32));

    i2s_initialized = true;
    last_report_ms = millis();

    Serial.println("[AUDIO] Half-duplex capture -> playback -> 500ms cooldown active");
    transitionTo(AudioState::CAPTURING);
}

void audioLoopLoop() {
    if (!i2s_initialized) return;

    uint32_t now = millis();

    switch (current_state) {
        case AudioState::CAPTURING: {
            // Read microphone (RX) and capture to recording buffer.
            size_t bytes_read = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
            size_t samples32 = bytes_read / sizeof(uint32_t);
            size_t frames = samples32 / 2;

            if (frames > 0) {
                size_t space_available = kCaptureSamples - samples_recorded;
                size_t to_copy = (frames < space_available) ? frames : space_available;

                for (size_t i = 0; i < to_copy; ++i) {
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
                    recording_buffer[samples_recorded + i] = static_cast<int16_t>(gained);
                }
                samples_recorded += to_copy;
            }

            // Write silence to TX during capture (no speaker output while listening,
            // keeps TX DMA fed without producing sound).
            i2s.write(tx_silence32, sizeof(tx_silence32));

            // Transition to PLAYBACK when capture buffer is full.
            if (samples_recorded >= kCaptureSamples) {
                samples_played = 0;
                transitionTo(AudioState::PLAYBACK);
            }
            break;
        }

        case AudioState::PLAYBACK: {
            // Drain RX (discard microphone input during playback to prevent feedback).
            size_t bytes_read = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
            (void)bytes_read;

            // Play from recording buffer.
            size_t remaining = samples_recorded - samples_played;
            size_t frames_to_play = (remaining < kReadFrames) ? remaining : kReadFrames;

            if (frames_to_play > 0) {
                for (size_t i = 0; i < frames_to_play; ++i) {
                    int16_t sample = recording_buffer[samples_played + i];
                    // sample already has gain applied from capture; no second gain.
                    int32_t sample_32 = static_cast<int32_t>(sample) * 65536;
                    tx_raw32[i * 2]     = static_cast<uint32_t>(sample_32);     // left slot
                    tx_raw32[i * 2 + 1] = static_cast<uint32_t>(sample_32);     // right slot
                }

                size_t bytes_to_write = frames_to_play * 2 * sizeof(uint32_t);
                size_t bytes_written = i2s.write(tx_raw32, bytes_to_write);
                size_t frames_written = bytes_written / (2 * sizeof(uint32_t));

                if (frames_written < frames_to_play) {
                    Serial.printf("[AUDIO] TX write incomplete: %u/%u frames\n",
                                  (unsigned int)frames_written, (unsigned int)frames_to_play);
                }
                // Advance playback position only by frames actually written.
                samples_played += frames_written;
            }

            // Transition to COOLDOWN only after all captured samples have been written.
            if (samples_played >= samples_recorded) {
                Serial.println("[AUDIO] Playback complete");
                cooldown_start_ms = now;
                Serial.println("[AUDIO] Cooldown started: 500 ms");
                transitionTo(AudioState::COOLDOWN);
            }
            break;
        }

        case AudioState::COOLDOWN: {
            // Drain RX (discard microphone input during cooldown).
            size_t bytes_read = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
            (void)bytes_read;

            // Write silence to TX during cooldown.
            i2s.write(tx_silence32, sizeof(tx_silence32));

            // Wait 500 ms before resuming capture (wrap-safe millis() comparison).
            if (static_cast<uint32_t>(now - cooldown_start_ms) >= kCooldownMs) {
                samples_recorded = 0;
                samples_played = 0;
                transitionTo(AudioState::CAPTURING);
            }
            break;
        }
    }

    // Periodic status log (concise, not per-block).
    if (now - last_report_ms >= kReportIntervalMs) {
        uint32_t free_heap = ESP.getFreeHeap();
        Serial.printf("[AUDIO] State=%s heap=%lu recorded=%u/%u played=%u/%u\n",
                      stateName(current_state),
                      (unsigned long)free_heap,
                      (unsigned int)samples_recorded, (unsigned int)kCaptureSamples,
                      (unsigned int)samples_played, (unsigned int)samples_recorded);
        last_report_ms = now;
    }
}

void audioLoopEnd() {
    if (!i2s_initialized) return;
    i2s.end();
    i2s_initialized = false;
}

}  // namespace DeskbotAudio
