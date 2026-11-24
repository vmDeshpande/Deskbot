#include "audio_loop.h"

#include <ESP_I2S.h>
#include <math.h>

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kReadFrames = 256;  // ~16 ms of mono audio at 16 kHz.
constexpr uint32_t kMaximumRecordingSeconds = 4;
constexpr size_t kMaximumRecordingSamples =
    static_cast<size_t>(kSampleRate) * kMaximumRecordingSeconds;
constexpr uint8_t kPushToTalkPin = 32;
constexpr uint32_t kButtonDebounceMs = 30;
constexpr uint32_t kReportIntervalMs = 2000;
constexpr float kInputGain = 0.5f;

enum class AudioState : uint8_t {
    IDLE,
    RECORDING,
    PLAYBACK
};

I2SClass i2s;
bool i2s_initialized = false;
AudioState current_state = AudioState::IDLE;

uint32_t last_report_ms = 0;
uint32_t button_raw_changed_ms = 0;
bool button_raw_pressed = false;
bool button_stable_pressed = false;
bool recording_limit_reported = false;

uint32_t rx_raw32[kReadFrames * 2];
int16_t recording_buffer[kMaximumRecordingSamples];
uint32_t tx_silence32[kReadFrames * 2];
uint32_t tx_raw32[kReadFrames * 2];

size_t samples_recorded = 0;
size_t samples_played = 0;

const char *stateName(AudioState state) {
    switch (state) {
        case AudioState::IDLE: return "IDLE";
        case AudioState::RECORDING: return "RECORDING";
        case AudioState::PLAYBACK: return "PLAYBACK";
    }
    return "UNKNOWN";
}

void transitionTo(AudioState new_state) {
    if (current_state == new_state) return;
    current_state = new_state;
    Serial.printf("[AUDIO] State: %s\n", stateName(new_state));
}

void beginRecording() {
    samples_recorded = 0;
    samples_played = 0;
    recording_limit_reported = false;
    transitionTo(AudioState::RECORDING);
    Serial.printf("[AUDIO] Push-to-talk pressed; recording (max %lu seconds).\n",
                  static_cast<unsigned long>(kMaximumRecordingSeconds));
}

void finishRecording() {
    if (samples_recorded == 0) {
        Serial.println("[AUDIO] Push-to-talk released; no audio captured.");
        transitionTo(AudioState::IDLE);
        return;
    }

    samples_played = 0;
    Serial.printf("[AUDIO] Push-to-talk released; captured %u samples (%.2f seconds).\n",
                  static_cast<unsigned int>(samples_recorded),
                  static_cast<float>(samples_recorded) / kSampleRate);
    transitionTo(AudioState::PLAYBACK);
}

void updatePushToTalkButton(uint32_t now) {
    const bool pressed = digitalRead(kPushToTalkPin) == LOW;  // INPUT_PULLUP, active-low.

    if (pressed != button_raw_pressed) {
        button_raw_pressed = pressed;
        button_raw_changed_ms = now;
    }

    if (button_stable_pressed != button_raw_pressed &&
        static_cast<uint32_t>(now - button_raw_changed_ms) >= kButtonDebounceMs) {
        button_stable_pressed = button_raw_pressed;

        if (button_stable_pressed && current_state == AudioState::IDLE) {
            beginRecording();
        } else if (!button_stable_pressed && current_state == AudioState::RECORDING) {
            finishRecording();
        }
    }
}

void captureMicrophoneBlock() {
    const size_t bytes_read = i2s.readBytes(reinterpret_cast<char *>(rx_raw32), sizeof(rx_raw32));
    const size_t samples32 = bytes_read / sizeof(uint32_t);
    const size_t frames = samples32 / 2;
    const size_t available = kMaximumRecordingSamples - samples_recorded;
    const size_t to_copy = (frames < available) ? frames : available;

    for (size_t i = 0; i < to_copy; ++i) {
        // INMP441 L/R is tied to GND, so audio is in the left slot (WS low).
        const uint32_t raw = rx_raw32[i * 2];
        // Convert the 24-bit, MSB-aligned microphone sample to signed PCM16.
        const int32_t sample_24 = static_cast<int32_t>(raw) >> 8;
        const int16_t sample_16 = static_cast<int16_t>(sample_24 >> 8);
        int32_t gained = static_cast<int32_t>(sample_16 * kInputGain);
        if (gained > 32767) gained = 32767;
        if (gained < -32768) gained = -32768;
        recording_buffer[samples_recorded + i] = static_cast<int16_t>(gained);
    }
    samples_recorded += to_copy;

    if (samples_recorded >= kMaximumRecordingSamples && !recording_limit_reported) {
        recording_limit_reported = true;
        Serial.println("[AUDIO] Maximum recording length reached; release the button to play it back.");
    }
}

void playRecordingBlock() {
    // Keep RX drained while the speaker is active; do not record speaker output.
    (void)i2s.readBytes(reinterpret_cast<char *>(rx_raw32), sizeof(rx_raw32));

    const size_t remaining = samples_recorded - samples_played;
    const size_t frames_to_play = (remaining < kReadFrames) ? remaining : kReadFrames;

    if (frames_to_play > 0) {
        for (size_t i = 0; i < frames_to_play; ++i) {
            const int32_t sample_32 = static_cast<int32_t>(recording_buffer[samples_played + i]) * 65536;
            tx_raw32[i * 2] = static_cast<uint32_t>(sample_32);
            tx_raw32[i * 2 + 1] = static_cast<uint32_t>(sample_32);
        }

        const size_t bytes_to_write = frames_to_play * 2 * sizeof(uint32_t);
        const size_t bytes_written = i2s.write(tx_raw32, bytes_to_write);
        samples_played += bytes_written / (2 * sizeof(uint32_t));
    }

    if (samples_played >= samples_recorded) {
        Serial.println("[AUDIO] Local recording playback complete.");
        samples_recorded = 0;
        samples_played = 0;
        transitionTo(AudioState::IDLE);
    }
}

}  // namespace

void audioLoopBegin() {
    if (i2s_initialized) return;

    pinMode(kPushToTalkPin, INPUT_PULLUP);
    button_raw_pressed = digitalRead(kPushToTalkPin) == LOW;
    button_stable_pressed = button_raw_pressed;
    button_raw_changed_ms = millis();

    // Pins: BCLK GPIO26, WS GPIO25, speaker DIN GPIO22, mic SD GPIO33.
    i2s.setPins(26, 25, 22, 33);

    Serial.println("[AUDIO] Initializing I2S...");
    if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT,
                   I2S_SLOT_MODE_STEREO, -1)) {
        Serial.printf("[AUDIO] ERROR: I2S begin() failed! lastError=%d\n", i2s.lastError());
        return;
    }

    if (!i2s.configureRX(kSampleRate, I2S_DATA_BIT_WIDTH_32BIT,
                         I2S_SLOT_MODE_STEREO, I2S_RX_TRANSFORM_NONE, -1)) {
        Serial.printf("[AUDIO] ERROR: configureRX failed! lastError=%d\n", i2s.lastError());
        i2s.end();
        return;
    }

    // Keep RX and TX enabled continuously. On original ESP32 full-duplex STD
    // mode, RX shares TX BCLK/WS; disabling either channel breaks the other.
    memset(tx_silence32, 0, sizeof(tx_silence32));
    i2s_initialized = true;
    last_report_ms = millis();

    Serial.printf("[AUDIO] I2S ready: %u Hz, 32-bit stereo.\n",
                  static_cast<unsigned int>(kSampleRate));
    Serial.printf("[AUDIO] Hold GPIO%u button to record; release to play back.\n",
                  static_cast<unsigned int>(kPushToTalkPin));
    Serial.printf("[AUDIO] Maximum recording length: %lu seconds.\n",
                  static_cast<unsigned long>(kMaximumRecordingSeconds));
    transitionTo(AudioState::IDLE);
}

void audioLoopLoop() {
    if (!i2s_initialized) return;

    const uint32_t now = millis();
    updatePushToTalkButton(now);

    switch (current_state) {
        case AudioState::IDLE:
            // Drain RX and keep TX DMA fed with silence while waiting for a press.
            (void)i2s.readBytes(reinterpret_cast<char *>(rx_raw32), sizeof(rx_raw32));
            (void)i2s.write(tx_silence32, sizeof(tx_silence32));
            break;

        case AudioState::RECORDING:
            captureMicrophoneBlock();
            // No speaker output while the user is speaking.
            (void)i2s.write(tx_silence32, sizeof(tx_silence32));
            break;

        case AudioState::PLAYBACK:
            playRecordingBlock();
            break;
    }

    if (static_cast<uint32_t>(now - last_report_ms) >= kReportIntervalMs) {
        Serial.printf("[AUDIO] State=%s recorded=%u/%u played=%u/%u heap=%lu\n",
                      stateName(current_state),
                      static_cast<unsigned int>(samples_recorded),
                      static_cast<unsigned int>(kMaximumRecordingSamples),
                      static_cast<unsigned int>(samples_played),
                      static_cast<unsigned int>(samples_recorded),
                      static_cast<unsigned long>(ESP.getFreeHeap()));
        last_report_ms = now;
    }
}

void audioLoopEnd() {
    if (!i2s_initialized) return;
    i2s.end();
    i2s_initialized = false;
}

}  // namespace DeskbotAudio
