#include "loopback_test.h"

#include <ESP_I2S.h>
#include <driver/i2s_std.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kBufferSamples = 32000;   // 2 seconds at 16 kHz
constexpr size_t kReadFrames = 512;        // Frames per read
constexpr uint32_t kReportIntervalMs = 2000;

enum class AudioState {
    LISTENING,
    PLAYING
};

I2SClass i2s;
bool i2s_initialized = false;
AudioState current_state = AudioState::LISTENING;
uint32_t last_report_ms = 0;

int16_t *recording_buffer = nullptr;

int16_t rx_raw16[kReadFrames];
uint32_t rx_raw32[kReadFrames * 2];
uint32_t tx_raw32[kReadFrames * 2];

// TX silence buffer (zeros) for LISTENING state
uint32_t tx_silence32[kReadFrames * 2];

int64_t left_sum_squares = 0;
int32_t left_peak = 0;
int32_t left_min = INT32_MAX;
int32_t left_max = INT32_MIN;
size_t left_count = 0;

size_t samples_recorded = 0;
size_t samples_played = 0;
size_t underrun_count = 0;
size_t overrun_count = 0;

constexpr float kOutputGain = 0.1f;

bool first_read_logged = false;
bool first_nonzero_logged = false;
uint32_t recording_start_ms = 0;
uint32_t playback_start_ms = 0;

const char *stateName(AudioState s) {
    switch (s) {
        case AudioState::LISTENING: return "LISTENING";
        case AudioState::PLAYING: return "PLAYING";
    }
    return "UNKNOWN";
}

void transitionTo(AudioState new_state) {
    Serial.printf("[LOOPBACK] State: %s\n", stateName(new_state));
    current_state = new_state;
}

}  // namespace

void loopbackTestBegin() {
    if (i2s_initialized) return;

    recording_buffer = (int16_t *)malloc(kBufferSamples * sizeof(int16_t));
    if (recording_buffer == nullptr) {
        Serial.println("[LOOPBACK] ERROR: Failed to allocate recording buffer!");
        return;
    }
    memset(recording_buffer, 0, kBufferSamples * sizeof(int16_t));

    // Initialize silence buffer for TX during LISTENING
    memset(tx_silence32, 0, sizeof(tx_silence32));

    i2s.setPins(26, 25, 22, 33);

    Serial.println("[LOOPBACK] Initializing I2S: 32-bit stereo RX + TX @ 16 kHz");
    if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO, -1)) {
        Serial.printf("[LOOPBACK] ERROR: I2S begin() failed! lastError=%d\n", i2s.lastError());
        free(recording_buffer);
        recording_buffer = nullptr;
        return;
    }

    if (!i2s.configureRX(kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO,
                         I2S_RX_TRANSFORM_NONE, -1)) {
        Serial.printf("[LOOPBACK] ERROR: configureRX failed! lastError=%d\n", i2s.lastError());
        i2s.end();
        free(recording_buffer);
        recording_buffer = nullptr;
        return;
    }

    // Both RX and TX remain ENABLED (full-duplex init with matching 32-bit stereo config)
    // Clocks stay running. We control data flow only.
    Serial.printf("[LOOPBACK] I2S initialized: RX rate=%lu bits=%d mode=%d | TX rate=%lu bits=%d mode=%d\n",
                  (unsigned long)i2s.rxSampleRate(), i2s.rxDataWidth(), i2s.rxSlotMode(),
                  (unsigned long)i2s.txSampleRate(), i2s.txDataWidth(), i2s.txSlotMode());

    i2s_initialized = true;
    samples_recorded = 0;
    samples_played = 0;
    underrun_count = 0;
    overrun_count = 0;
    last_report_ms = millis();
    first_read_logged = false;
    first_nonzero_logged = false;

    recording_start_ms = millis();
    Serial.println("[LOOPBACK] Recording started");
    transitionTo(AudioState::LISTENING);

    Serial.println("[LOOPBACK] Started: Half-duplex loopback (shared clocks always on), 2s record / 2s playback, 10% output gain");
    Serial.printf("[LOOPBACK] Recording buffer: %u samples (%u KB)\n",
                  (unsigned)kBufferSamples, (unsigned)(kBufferSamples * 2 / 1024));
}

void loopbackTestLoop() {
    if (!i2s_initialized) return;

    uint32_t now = millis();

    switch (current_state) {
        case AudioState::LISTENING: {
            // Read microphone (RX)
            size_t bytes_read = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
            size_t samples32 = bytes_read / sizeof(uint32_t);

            if (!first_read_logged && samples32 > 0) {
                Serial.printf("[LOOPBACK] First RX read: bytes=%u samples32=%u (frames=%u)\n",
                              (unsigned)bytes_read, (unsigned)samples32, (unsigned)(samples32 / 2));
                first_read_logged = true;
            }

            if (bytes_read == 0 && samples32 == 0) {
                int err = i2s.lastError();
                if (err != ESP_OK) {
                    Serial.printf("[LOOPBACK] RX read error: lastError=%d (%s)\n",
                                  err, esp_err_to_name((esp_err_t)err));
                }
            }

            // Write silence to TX to keep DMA fed (prevents underrun, keeps clocks happy)
            // This is non-blocking in practice because silence buffer is pre-filled
            i2s.write(tx_silence32, sizeof(tx_silence32));

            if (samples32 > 0) {
                size_t frames = samples32 / 2;
                left_count = 0;

                for (size_t i = 0; i < frames; ++i) {
                    uint32_t raw_left = rx_raw32[i * 2];
                    int32_t sample_24 = static_cast<int32_t>(raw_left >> 8);
                    int16_t sample_16 = static_cast<int16_t>(sample_24 >> 8);
                    rx_raw16[left_count++] = sample_16;

                    left_sum_squares += (int64_t)sample_16 * sample_16;
                    if (sample_16 > left_peak) left_peak = sample_16;
                    if (-sample_16 > left_peak) left_peak = -sample_16;
                    if (sample_16 < left_min) left_min = sample_16;
                    if (sample_16 > left_max) left_max = sample_16;

                    if (!first_nonzero_logged && sample_16 != 0) {
                        Serial.printf("[LOOPBACK] First nonzero mic sample: raw32=0x%08lX sample_24=%ld sample_16=%d\n",
                                      (unsigned long)raw_left, (long)sample_24, sample_16);
                        first_nonzero_logged = true;
                    }
                }

                size_t space_available = kBufferSamples - samples_recorded;
                if (left_count > space_available) {
                    overrun_count++;
                    left_count = space_available;
                }

                if (left_count > 0) {
                    size_t first_chunk = (samples_recorded + left_count <= kBufferSamples)
                                         ? left_count : kBufferSamples - samples_recorded;
                    memcpy(&recording_buffer[samples_recorded], rx_raw16, first_chunk * sizeof(int16_t));
                    if (first_chunk < left_count) {
                        memcpy(recording_buffer, &rx_raw16[first_chunk], (left_count - first_chunk) * sizeof(int16_t));
                    }
                    samples_recorded += left_count;
                }

                if (samples_recorded >= kBufferSamples) {
                    uint32_t recording_duration_ms = millis() - recording_start_ms;
                    float left_rms = (left_count > 0) ? sqrtf((float)left_sum_squares / left_count) : 0.0f;
                    Serial.printf("[LOOPBACK] Recording complete: %u samples in %lu ms (rms=%.1f peak=%ld min=%ld max=%ld)\n",
                                  (unsigned)samples_recorded, (unsigned long)recording_duration_ms,
                                  left_rms, (long)left_peak, (long)left_min, (long)left_max);
                    samples_played = 0;
                    playback_start_ms = millis();
                    transitionTo(AudioState::PLAYING);
                }
            }
            break;
        }

        case AudioState::PLAYING: {
            // Read from RX but discard (keep RX DMA fed, clocks running)
            // Use a small dummy read to keep RX channel active
            uint32_t dummy_rx32[8];
            i2s.readBytes((char *)dummy_rx32, sizeof(dummy_rx32));

            size_t remaining = kBufferSamples - samples_played;
            size_t frames_to_play = (remaining < kReadFrames) ? remaining : kReadFrames;

            if (frames_to_play > 0) {
                for (size_t i = 0; i < frames_to_play; ++i) {
                    int16_t sample = recording_buffer[samples_played];
                    int16_t gained = static_cast<int16_t>(sample * kOutputGain);

                    int32_t sample_32 = static_cast<int32_t>(gained) * 65536;

                    tx_raw32[i * 2] = static_cast<uint32_t>(sample_32);
                    tx_raw32[i * 2 + 1] = static_cast<uint32_t>(sample_32);
                }

                size_t bytes_to_write = frames_to_play * 2 * sizeof(uint32_t);
                size_t bytes_written = i2s.write(tx_raw32, bytes_to_write);
                size_t frames_written = bytes_written / (2 * sizeof(uint32_t));

                if (frames_written < frames_to_play) {
                    underrun_count++;
                }
                samples_played += frames_written;

                if (samples_played >= kBufferSamples) {
                    uint32_t playback_duration_ms = millis() - playback_start_ms;
                    Serial.printf("[LOOPBACK] Speaker playback complete: %u samples in %lu ms\n",
                                  (unsigned)samples_played, (unsigned long)playback_duration_ms);
                    samples_recorded = 0;
                    recording_start_ms = millis();
                    first_read_logged = false;
                    first_nonzero_logged = false;
                    Serial.println("[LOOPBACK] Recording started");
                    transitionTo(AudioState::LISTENING);
                }
            }
            break;
        }
    }

    if (now - last_report_ms >= kReportIntervalMs) {
        uint32_t free_heap = ESP.getFreeHeap();
        Serial.printf("[LOOPBACK] State=%s heap=%lu",
                      stateName(current_state), (unsigned long)free_heap);
        if (current_state == AudioState::LISTENING) {
            float left_rms = (left_count > 0) ? sqrtf((float)left_sum_squares / left_count) : 0.0f;
            Serial.printf(" samples_recorded=%u/%u rms=%.1f peak=%ld",
                          (unsigned)samples_recorded, (unsigned)kBufferSamples,
                          left_rms, (long)left_peak);
        } else if (current_state == AudioState::PLAYING) {
            Serial.printf(" samples_played=%u/%u underruns=%lu",
                          (unsigned)samples_played, (unsigned)kBufferSamples,
                          (unsigned long)underrun_count);
        }
        Serial.println();

        left_sum_squares = 0;
        left_peak = 0;
        left_min = INT32_MAX;
        left_max = INT32_MIN;
        last_report_ms = now;
    }
}

void loopbackTestEnd() {
    if (!i2s_initialized) return;

    i2s.end();

    if (recording_buffer != nullptr) {
        free(recording_buffer);
        recording_buffer = nullptr;
    }
    i2s_initialized = false;
    Serial.println("[LOOPBACK] Stopped");
}

}  // namespace DeskbotAudio