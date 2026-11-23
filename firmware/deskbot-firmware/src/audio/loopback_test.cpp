#include "loopback_test.h"

#include <ESP_I2S.h>
#include <driver/i2s_common.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kBufferSamples = 32000;   // 2 seconds @ 16 kHz, mono PCM16
constexpr size_t kReadFrames = 512;        // I2S frames per read / write (stereo 32-bit)
constexpr uint32_t kReportIntervalMs = 1000;
constexpr size_t kDrainReads = 1;          // drain reads after re-enabling RX
constexpr uint32_t kTxWriteTimeoutMs = 100; // per-attempt timeout for TX writes
constexpr size_t kTxMaxRetries = 8;         // max retries for a short/failed TX write

enum class AudioState {
    LISTENING,
    PLAYING
};

I2SClass i2s;
bool i2s_initialized = false;
bool rx_enabled = false;                   // RX channel currently enabled (capturing)?
bool tx_stalled = false;                   // TX write stalled (consecutive failures)
AudioState current_state = AudioState::LISTENING;
uint32_t last_report_ms = 0;
uint32_t last_transition_ms = 0;
size_t state_transitions = 0;

int16_t *recording_buffer = nullptr;

uint32_t rx_raw32[kReadFrames * 2];        // stereo 32-bit read buffer (2 slots/frame)
int16_t rx_raw16[kReadFrames];             // converted mono PCM16
uint32_t tx_raw32[kReadFrames * 2];        // stereo 32-bit write buffer
uint32_t tx_silence32[kReadFrames * 2];    // silence (zeros) for TX during LISTENING

// Counters
size_t samples_recorded = 0;
size_t samples_played = 0;
size_t rx_reads = 0;
size_t rx_read_bytes = 0;
size_t rx_read_errors = 0;
size_t rx_read_timeouts = 0;
size_t rx_drain_reads = 0;
size_t tx_writes = 0;
size_t tx_write_bytes = 0;
size_t tx_write_errors = 0;
size_t tx_short_writes = 0;
size_t tx_retries = 0;
size_t tx_stall_events = 0;
size_t overrun_count = 0;
size_t underrun_count = 0;

constexpr float kOutputGain = 0.1f;

uint32_t recording_start_ms = 0;
uint32_t playback_start_ms = 0;

const char *stateName(AudioState s) {
    switch (s) {
        case AudioState::LISTENING: return "LISTENING";
        case AudioState::PLAYING: return "PLAYING";
    }
    return "UNKNOWN";
}

// RX enable/disable wrap the IDF channel lifecycle directly. The Arduino I2S
// wrapper exposes no public, clock-safe independent channel enable/disable, so
// the IDF calls are used on the handle obtained via i2s.rxChan().
//
// Lifecycle requirements (verified in esp_driver_i2s/i2s_common.c):
//   i2s_channel_disable() requires the channel state == RUNNING, transitions to
//   READY, waits for any in-progress read to quit, stops DMA and resets the RX
//   queue. i2s_channel_enable() requires state == READY, restarts DMA, resets
//   the RX queue (drains stale samples) and returns to RUNNING.
//
// Architecture note (ESP32 HW v1, IDF 6.1): in full-duplex STD mode
// i2s_std_set_slot() calls i2s_ll_share_bck_ws(true), which makes RX slave to
// TX's BCLK/WS (conf.sig_loopback). TX is the clock master. Disabling TX
// (i2s_channel_disable on the TX channel -> i2s_ll_tx_stop, tx_start=0) stops
// the shared BCLK/WS clock, so RX reads time out (ESP_ERR_TIMEOUT) and the
// disable/enable race can surface ESP_ERR_INVALID_STATE. TX is therefore never
// disabled: it stays enabled as the shared-clock master for the entire run.
// RX alone is gated to start/stop microphone capture. This is genuine
// half-duplex for the microphone path (capture is stopped during playback);
// TX cannot be stopped without stopping the clock RX depends on.
esp_err_t rxEnable() {
    if (i2s.rxChan() == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = i2s_channel_enable(i2s.rxChan());
    if (err == ESP_OK) {
        rx_enabled = true;
    }
    return err;
}

esp_err_t rxDisable() {
    if (i2s.rxChan() == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = i2s_channel_disable(i2s.rxChan());
    if (err == ESP_OK) {
        rx_enabled = false;
    }
    return err;
}

// Verify TX channel is still enabled (RUNNING). Returns true if TX can accept
// writes. Logs if TX is not enabled, which would indicate the shared clock
// was disturbed by an RX disable/enable cycle.
bool txEnabled() {
    if (i2s.txChan() == NULL) {
        return false;
    }
    i2s_chan_info_t info;
    esp_err_t err = i2s_channel_get_info(i2s.txChan(), &info);
    if (err != ESP_OK) {
        Serial.printf("[LOOPBACK] ERROR: i2s_channel_get_info(tx) failed: %d (%s)\n",
                      err, esp_err_to_name(err));
        return false;
    }
    if (!info.is_enabled) {
        Serial.printf("[LOOPBACK] ERROR: TX channel not enabled! dir=%d mode=%d\n",
                      (int)info.dir, (int)info.mode);
    }
    return info.is_enabled;
}

// Write all requested bytes to TX with bounded retries. Returns total bytes
// actually written. Every short/failed attempt is logged with requested vs
// actual bytes, lastError, and timeout status. Never silently discards audio.
size_t txWriteAll(const void *buffer, size_t size) {
    const uint8_t *data = (const uint8_t *)buffer;
    size_t total_written = 0;
    size_t retries = 0;

    while (total_written < size) {
        size_t remaining = size - total_written;
        size_t bytes_written = i2s.write(data + total_written, remaining);
        int tx_err = i2s.lastError();
        tx_writes++;

        if (bytes_written > 0) {
            total_written += bytes_written;
            retries = 0;  // progress made, reset retry count
            continue;
        }

        // Zero bytes written: error or timeout. Log in detail.
        tx_write_errors++;
        tx_short_writes++;
        retries++;
        tx_retries++;

        Serial.printf("[LOOPBACK] TX write stalled: requested=%u written=%u err=%d (%s) attempt=%u/%u\n",
                      (unsigned)remaining, (unsigned)bytes_written, tx_err,
                      esp_err_to_name((esp_err_t)tx_err), (unsigned)retries, (unsigned)kTxMaxRetries);

        if (retries >= kTxMaxRetries) {
            tx_stalled = true;
            tx_stall_events++;
            Serial.printf("[LOOPBACK] TX stalled after %u retries, %u bytes still pending\n",
                          (unsigned)kTxMaxRetries, (unsigned)remaining);
            break;
        }

        // Brief yield to allow DMA to return buffers to the queue before retrying.
        yield();
    }

    tx_write_bytes += total_written;
    return total_written;
}

void transitionTo(AudioState new_state) {
    if (current_state == new_state) {
        return;
    }
    AudioState prev_state = current_state;
    current_state = new_state;
    state_transitions++;
    last_transition_ms = millis();
    Serial.printf("[LOOPBACK] Transition %s -> %s\n", stateName(prev_state), stateName(new_state));

    uint32_t now = millis();
    if (new_state == AudioState::PLAYING) {
        // Required behavior #2: stop microphone capture before playback.
        esp_err_t err = rxDisable();
        Serial.printf("[LOOPBACK] RX disabled (stop mic capture): err=%d (%s)\n",
                      err, esp_err_to_name(err));
        // Verify TX is still enabled after RX disable (shared clock check).
        Serial.printf("[LOOPBACK] TX enabled after RX disable: %s\n",
                      txEnabled() ? "yes" : "NO");
        samples_played = 0;
        playback_start_ms = now;
        tx_stalled = false;
        Serial.println("[LOOPBACK] Playback started");
    } else {
        // Required behavior #4: stop playback and resume microphone capture.
        esp_err_t err = rxEnable();
        Serial.printf("[LOOPBACK] RX enabled (resume mic capture): err=%d (%s)\n",
                      err, esp_err_to_name(err));
        // Drain any stale RX samples left in the DMA pipe before capturing.
        for (size_t d = 0; d < kDrainReads; ++d) {
            size_t nb = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
            rx_drain_reads++;
            (void)nb;
        }
        samples_recorded = 0;
        recording_start_ms = now;
        Serial.println("[LOOPBACK] Recording started");
    }
}

}  // namespace

void loopbackTestBegin() {
    if (i2s_initialized) {
        return;
    }

    recording_buffer = (int16_t *)malloc(kBufferSamples * sizeof(int16_t));
    if (recording_buffer == nullptr) {
        Serial.println("[LOOPBACK] ERROR: Failed to allocate recording buffer!");
        return;
    }
    memset(recording_buffer, 0, kBufferSamples * sizeof(int16_t));
    memset(tx_silence32, 0, sizeof(tx_silence32));

    // Pins: BCLK GPIO26, WS GPIO25, speaker DIN GPIO22, mic SD GPIO33.
    i2s.setPins(26, 25, 22, 33);

    Serial.println("[LOOPBACK] Initializing I2S: Standard Philips, 16 kHz, 32-bit stereo, RX + TX");
    Serial.println("[LOOPBACK] Architecture: TX is the shared-clock master and stays enabled always;");
    Serial.println("[LOOPBACK] RX is gated on/off to start/stop microphone capture. Disabling TX would");
    Serial.println("[LOOPBACK] stop the shared BCLK/WS clock (RX loops back TX's clock) and break RX.");

    if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO, -1)) {
        Serial.printf("[LOOPBACK] ERROR: I2S begin() failed! lastError=%d (%s)\n",
                      i2s.lastError(), esp_err_to_name((esp_err_t)i2s.lastError()));
        free(recording_buffer);
        recording_buffer = nullptr;
        return;
    }

    if (!i2s.configureRX(kSampleRate, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO,
                         I2S_RX_TRANSFORM_NONE, -1)) {
        Serial.printf("[LOOPBACK] ERROR: configureRX failed! lastError=%d (%s)\n",
                      i2s.lastError(), esp_err_to_name((esp_err_t)i2s.lastError()));
        i2s.end();
        free(recording_buffer);
        recording_buffer = nullptr;
        return;
    }

    Serial.printf("[LOOPBACK] I2S initialized: TX rate=%lu bits=%d mode=%d | RX rate=%lu bits=%d mode=%d\n",
                  (unsigned long)i2s.txSampleRate(), i2s.txDataWidth(), i2s.txSlotMode(),
                  (unsigned long)i2s.rxSampleRate(), i2s.rxDataWidth(), i2s.rxSlotMode());

    i2s_initialized = true;
    rx_enabled = true;   // begin() enables both channels (RUNNING)
    samples_recorded = 0;
    samples_played = 0;
    tx_stalled = false;
    overrun_count = 0;
    underrun_count = 0;
    rx_reads = 0;
    rx_read_bytes = 0;
    rx_read_errors = 0;
    rx_read_timeouts = 0;
    rx_drain_reads = 0;
    tx_writes = 0;
    tx_write_bytes = 0;
    tx_write_errors = 0;
    tx_short_writes = 0;
    tx_retries = 0;
    tx_stall_events = 0;
    state_transitions = 0;
    last_report_ms = millis();
    last_transition_ms = last_report_ms;

    recording_start_ms = millis();
    Serial.println("[LOOPBACK] Recording started");
    transitionTo(AudioState::LISTENING);

    Serial.println("[LOOPBACK] Started: reliable loopback, 2 s record / 2 s playback, 10% output gain");
    Serial.printf("[LOOPBACK] Recording buffer: %u samples (%u KB)\n",
                  (unsigned)kBufferSamples, (unsigned)(kBufferSamples * 2 / 1024));
    Serial.printf("[LOOPBACK] TX write: %u frames/write, %u bytes/write, timeout=%ums, max retries=%u\n",
                  (unsigned)kReadFrames, (unsigned)(kReadFrames * 2 * sizeof(uint32_t)),
                  (unsigned)kTxWriteTimeoutMs, (unsigned)kTxMaxRetries);
}

void loopbackTestLoop() {
    if (!i2s_initialized) {
        return;
    }

    uint32_t now = millis();

    switch (current_state) {
        case AudioState::LISTENING: {
            // Ensure the microphone is capturing.
            if (!rx_enabled) {
                esp_err_t err = rxEnable();
                Serial.printf("[LOOPBACK] RX re-enabled: err=%d (%s)\n",
                              err, esp_err_to_name(err));
            }

            // --- Capture: read microphone (RX) ---
            size_t bytes_read = i2s.readBytes((char *)rx_raw32, sizeof(rx_raw32));
            int rx_err = i2s.lastError();
            rx_reads++;
            rx_read_bytes += bytes_read;
            if (rx_err == ESP_ERR_TIMEOUT) {
                rx_read_timeouts++;
            } else if (rx_err != ESP_OK) {
                rx_read_errors++;
            }
            if (bytes_read == 0 && rx_err != ESP_OK) {
                Serial.printf("[LOOPBACK] RX read error: %d (%s)\n", rx_err, esp_err_to_name((esp_err_t)rx_err));
            }

            // Keep the TX clock/DMA fed with silence while listening (TX stays enabled).
            size_t silence_written = txWriteAll(tx_silence32, sizeof(tx_silence32));
            if (silence_written != sizeof(tx_silence32)) {
                Serial.printf("[LOOPBACK] TX silence write incomplete: %u/%u bytes\n",
                              (unsigned)silence_written, (unsigned)sizeof(tx_silence32));
            }

            // --- Convert INMP441 24-bit MSB-aligned (slot 0 = left, INMP441 L/R=GND) to PCM16 ---
            size_t samples32 = bytes_read / sizeof(uint32_t);
            size_t frames = samples32 / 2;
            size_t left_count = 0;

            for (size_t i = 0; i < frames; ++i) {
                uint32_t raw = rx_raw32[i * 2];                      // left slot (WS low)
                int32_t sample_24 = static_cast<int32_t>(raw) >> 8;  // sign-extended 24-bit
                int16_t sample_16 = static_cast<int16_t>(sample_24 >> 8);
                rx_raw16[left_count++] = sample_16;
            }

            // --- Accumulate into the recording buffer with overrun protection ---
            size_t space_available = kBufferSamples - samples_recorded;
            size_t to_copy = (left_count < space_available) ? left_count : space_available;
            if (to_copy < left_count) {
                overrun_count++;
            }
            if (to_copy > 0) {
                size_t first_chunk = (samples_recorded + to_copy <= kBufferSamples)
                                         ? to_copy
                                         : kBufferSamples - samples_recorded;
                memcpy(&recording_buffer[samples_recorded], rx_raw16, first_chunk * sizeof(int16_t));
                if (first_chunk < to_copy) {
                    memcpy(recording_buffer, &rx_raw16[first_chunk],
                           (to_copy - first_chunk) * sizeof(int16_t));
                }
                samples_recorded += to_copy;
            }

            if (samples_recorded >= kBufferSamples) {
                uint32_t capture_duration_ms = now - recording_start_ms;
                Serial.printf("[LOOPBACK] Capture complete: %u samples in %lu ms\n",
                              (unsigned)samples_recorded, (unsigned long)capture_duration_ms);
                transitionTo(AudioState::PLAYING);
            }
            break;
        }

        case AudioState::PLAYING: {
            // RX is disabled here: microphone capture is stopped during playback.
            if (!txEnabled()) {
                Serial.printf("[LOOPBACK] ERROR: TX not enabled in PLAYING state, skipping write\n");
                break;
            }

            size_t remaining = kBufferSamples - samples_played;
            size_t frames_to_play = (remaining < kReadFrames) ? remaining : kReadFrames;

            if (frames_to_play > 0) {
                for (size_t i = 0; i < frames_to_play; ++i) {
                    int16_t sample = recording_buffer[samples_played];
                    int16_t gained = static_cast<int16_t>(sample * kOutputGain);
                    // PCM16 -> 32-bit MSB-aligned slot (bits 31-16), duplicated L+R.
                    uint32_t slot = static_cast<uint32_t>(static_cast<int32_t>(gained) << 16);
                    tx_raw32[i * 2] = slot;      // left slot
                    tx_raw32[i * 2 + 1] = slot;  // right slot
                }

                size_t bytes_to_write = frames_to_play * 2 * sizeof(uint32_t);
                size_t bytes_written = txWriteAll(tx_raw32, bytes_to_write);

                size_t frames_written = bytes_written / (2 * sizeof(uint32_t));
                if (frames_written < frames_to_play) {
                    underrun_count++;
                }
                // Only advance the sample counter by bytes actually written.
                samples_played += frames_written;

                if (bytes_written != bytes_to_write) {
                    Serial.printf("[LOOPBACK] TX playback write incomplete: %u/%u bytes (frames=%u/%u)\n",
                                  (unsigned)bytes_written, (unsigned)bytes_to_write,
                                  (unsigned)frames_written, (unsigned)frames_to_play);
                }

                if (samples_played >= kBufferSamples) {
                    uint32_t playback_duration_ms = now - playback_start_ms;
                    Serial.printf("[LOOPBACK] Playback complete: %u samples in %lu ms\n",
                                  (unsigned)samples_played, (unsigned long)playback_duration_ms);
                    transitionTo(AudioState::LISTENING);
                }
            }
            break;
        }
    }

    // --- Periodic statistics report ---
    if (now - last_report_ms >= kReportIntervalMs) {
        uint32_t free_heap = ESP.getFreeHeap();
        Serial.printf("[LOOPBACK] State=%s heap=%lu transitions=%u",
                      stateName(current_state), (unsigned long)free_heap,
                      (unsigned)state_transitions);
        if (current_state == AudioState::LISTENING) {
            Serial.printf(" samples_recorded=%u/%u rx_reads=%u rx_bytes=%u rx_timeouts=%u rx_errors=%u",
                          (unsigned)samples_recorded, (unsigned)kBufferSamples,
                          (unsigned)rx_reads, (unsigned)rx_read_bytes,
                          (unsigned)rx_read_timeouts, (unsigned)rx_read_errors);
        } else if (current_state == AudioState::PLAYING) {
            Serial.printf(" samples_played=%u/%u tx_writes=%u tx_bytes=%u tx_errors=%u tx_short=%u tx_retries=%u tx_stalls=%u underruns=%u",
                          (unsigned)samples_played, (unsigned)kBufferSamples,
                          (unsigned)tx_writes, (unsigned)tx_write_bytes,
                          (unsigned)tx_write_errors, (unsigned)tx_short_writes,
                          (unsigned)tx_retries, (unsigned)tx_stall_events,
                          (unsigned)underrun_count);
        }
        Serial.printf(" overruns=%u rx_drain=%u\n",
                      (unsigned)overrun_count, (unsigned)rx_drain_reads);

        // Reset rolling per-interval counters.
        rx_reads = 0;
        rx_read_bytes = 0;
        rx_read_errors = 0;
        rx_read_timeouts = 0;
        tx_writes = 0;
        tx_write_bytes = 0;
        tx_write_errors = 0;
        tx_short_writes = 0;
        tx_retries = 0;
        last_report_ms = now;
    }
}

void loopbackTestEnd() {
    if (!i2s_initialized) {
        return;
    }

    // Stop microphone capture before tearing down.
    if (rx_enabled) {
        esp_err_t err = rxDisable();
        Serial.printf("[LOOPBACK] RX disabled on end: err=%d (%s)\n", err, esp_err_to_name(err));
    }

    i2s.end();

    if (recording_buffer != nullptr) {
        free(recording_buffer);
        recording_buffer = nullptr;
    }
    i2s_initialized = false;
    rx_enabled = false;
    tx_stalled = false;
    Serial.println("[LOOPBACK] Stopped");
}

}  // namespace DeskbotAudio
