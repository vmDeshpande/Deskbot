#include "audio_loop.h"

#include <ESP_I2S.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <time.h>

#include "../protocol/deskbot_protocol.h"
#include "secrets.h"

namespace DeskbotAudio {
namespace {

constexpr uint32_t kSampleRate = 16000;
constexpr size_t kI2sReadFrames = 256;      // I²S stereo frames per read (~16 ms @ 16 kHz)
constexpr size_t kPcmFrameSamples = 320;     // PCM16 mono samples per WebSocket binary frame (~20 ms)
constexpr uint8_t kPushToTalkPin = 32;
constexpr uint32_t kButtonDebounceMs = 30;
constexpr uint32_t kReportIntervalMs = 2000;
constexpr float kInputGain = 0.5f;
constexpr char kAudioWsPath[] = "/ws/audio";
constexpr char kAudioWsSubprotocol[] = "deskbot.audio.v1";
constexpr uint32_t kAudioStopTimeoutMs = 10000;

enum class AudioState : uint8_t {
    IDLE,
    PENDING_START,   // Waiting for clock sync before sending audio.start
    STREAMING,
    STOPPING,
};

enum class AudioWsState : uint8_t {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
};

I2SClass i2s;
WebSocketsClient audioClient;
bool i2s_initialized = false;
AudioState audio_state = AudioState::IDLE;
AudioWsState audio_ws_state = AudioWsState::DISCONNECTED;

uint32_t last_report_ms = 0;
uint32_t button_raw_changed_ms = 0;
uint32_t audio_stop_deadline_ms = 0;
bool button_raw_pressed = false;
bool button_stable_pressed = false;
bool audio_start_acked = false;   // Backend has acknowledged audio.start
bool audio_finish_sent = false;   // audio.finish already sent for current session
char recording_id[37] = {};

uint32_t rx_raw32[kI2sReadFrames * 2];
uint32_t tx_silence32[kI2sReadFrames * 2];
int16_t pcm16_accum[kPcmFrameSamples];
size_t pcm16_accum_count = 0;

size_t frames_sent = 0;
size_t bytes_sent = 0;

void audioWsEvent(WStype_t type, uint8_t *payload, size_t length);
void handleAudioText(char *text, size_t length);

void audioWsEvent(WStype_t type, uint8_t *payload, size_t length) {
    switch (type) {
        case WStype_CONNECTED:
            audio_ws_state = AudioWsState::CONNECTED;
            Serial.println("[AUDIO] Audio WebSocket connected.");
            break;
        case WStype_DISCONNECTED: {
            const char *reason = reinterpret_cast<const char *>(payload);
            if (length > 0 && reason[0] != '\0') {
                Serial.printf("[AUDIO] Audio WebSocket disconnected: %s\n", reason);
            } else {
                Serial.println("[AUDIO] Audio WebSocket disconnected.");
            }
            audio_ws_state = AudioWsState::DISCONNECTED;
            audio_start_acked = false;
            audio_finish_sent = false;
            if (audio_state == AudioState::STREAMING || audio_state == AudioState::STOPPING) {
                audio_state = AudioState::IDLE;
                pcm16_accum_count = 0;
                recording_id[0] = '\0';
                Serial.println("[AUDIO] State: IDLE (WS disconnected)");
            }
            break;
        }
        case WStype_TEXT:
            handleAudioText(reinterpret_cast<char *>(payload), length);
            break;
        default:
            break;
    }
}

void handleAudioText(char *text, size_t length) {
    // Log incoming control message for diagnostics (first 120 chars only).
    char logbuf[128];
    size_t copylen = (length < sizeof(logbuf) - 1) ? length : sizeof(logbuf) - 1;
    memcpy(logbuf, text, copylen);
    logbuf[copylen] = '\0';
    Serial.printf("[AUDIO] RX text (%u bytes): %s\n", (unsigned int)length, logbuf);

    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, text, length);
    if (error) {
        Serial.printf("[AUDIO] Failed to parse audio WS message: %s\n", error.c_str());
        return;
    }
    const char *message_type = doc["message_type"];
    if (message_type == nullptr) return;
    if (strcmp(message_type, "audio.started") == 0) {
        const char *rid = doc["payload"]["recording_id"];
        if (rid) {
            Serial.printf("[AUDIO] Backend acknowledged audio.start (recording_id=%s).\n", rid);
        } else {
            Serial.println("[AUDIO] Backend acknowledged audio.start.");
        }
        audio_start_acked = true;
    } else if (strcmp(message_type, "audio.completed") == 0) {
        const char *text_result = doc["payload"]["text"];
        if (text_result) {
            Serial.printf("[AUDIO] Transcription: %s\n", text_result);
        }
        audio_state = AudioState::IDLE;
        pcm16_accum_count = 0;
        recording_id[0] = '\0';
        audio_start_acked = false;
        audio_finish_sent = false;
        Serial.println("[AUDIO] State: IDLE");
    } else if (strcmp(message_type, "audio.error") == 0) {
        const char *code = doc["payload"]["code"];
        if (code) {
            Serial.printf("[AUDIO] Server error: %s\n", code);
        } else {
            Serial.println("[AUDIO] Server error.");
        }
        audio_state = AudioState::IDLE;
        pcm16_accum_count = 0;
        recording_id[0] = '\0';
        audio_start_acked = false;
        audio_finish_sent = false;
        Serial.println("[AUDIO] State: IDLE (server error)");
    } else if (strcmp(message_type, "audio.partial") == 0) {
        const char *partial_text = doc["payload"]["text"];
        if (partial_text) {
            Serial.printf("[AUDIO] Partial: %s\n", partial_text);
        }
    }
}

void connectAudioWebSocket() {
    if (audio_ws_state != AudioWsState::DISCONNECTED) return;
    audio_ws_state = AudioWsState::CONNECTING;
    audioClient.begin(DESKBOT_WS_HOST, DESKBOT_WS_PORT, kAudioWsPath, kAudioWsSubprotocol);
    String authorization = "Bearer ";
    authorization += DESKBOT_DEVICE_TOKEN;
    audioClient.setAuthorization(authorization.c_str());
    audioClient.onEvent(audioWsEvent);
    Serial.println("[AUDIO] Audio WebSocket connecting...");
}

void disconnectAudioWebSocket() {
    if (audio_ws_state == AudioWsState::DISCONNECTED) return;
    audioClient.disconnect();
    audio_ws_state = AudioWsState::DISCONNECTED;
    audio_start_acked = false;
}

bool sendAudioStart() {
    char timestamp[32] = {};
    if (!DeskbotProtocol::formatUtcTimestamp(timestamp, sizeof(timestamp))) {
        Serial.println("[AUDIO] Clock not synchronized; cannot send audio.start.");
        return false;
    }
    char message_id[37] = {};
    DeskbotProtocol::makeUuidV4(message_id);
    char rec_id[37] = {};
    DeskbotProtocol::makeUuidV4(rec_id);
    memcpy(recording_id, rec_id, sizeof(recording_id));
    StaticJsonDocument<384> doc;
    doc["protocol_version"] = "1.0";
    doc["message_id"] = message_id;
    doc["message_type"] = "audio.start";
    doc["timestamp"] = timestamp;
    JsonObject payload = doc.createNestedObject("payload");
    payload["recording_id"] = recording_id;
    payload["sample_rate"] = static_cast<int>(kSampleRate);
    payload["channels"] = 1;
    payload["format"] = "pcm_s16le";
    const size_t required_size = measureJson(doc);
    if (required_size == 0) {
        Serial.println("[AUDIO] ERROR: measureJson returned 0 for audio.start.");
        return false;
    }
    char output[DeskbotProtocol::kOutputBufferBytes];
    size_t len = serializeJson(doc, output, sizeof(output));
    Serial.printf("[AUDIO] TX audio.start: required=%u capacity=%u serialized=%u\n",
                  (unsigned int)required_size,
                  (unsigned int)sizeof(output),
                  (unsigned int)len);
    if (len == 0) {
        Serial.println("[AUDIO] ERROR: serializeJson returned 0 for audio.start.");
        return false;
    }
    if (len > sizeof(output) - 1) {
        Serial.printf("[AUDIO] ERROR: JSON truncated (%u > %u bytes available).\n",
                      (unsigned int)len, (unsigned int)(sizeof(output) - 1));
        return false;
    }
    output[len] = '\0';
    if (audioClient.isConnected()) {
        Serial.printf("[AUDIO] TX audio.start (%u bytes): %s\n", (unsigned int)len, output);
        if (audioClient.sendTXT(reinterpret_cast<const uint8_t *>(output), len)) {
            Serial.println("[AUDIO] audio.start sent.");
            return true;
        }
        Serial.println("[AUDIO] audio.start send failed.");
    }
    return false;
}

void sendAudioFinish() {
    if (!recording_id[0]) return;
    if (audio_finish_sent) return;
    char timestamp[32] = {};
    if (!DeskbotProtocol::formatUtcTimestamp(timestamp, sizeof(timestamp))) {
        Serial.println("[AUDIO] Clock not synchronized; cannot send audio.finish.");
        return;
    }
    char message_id[37] = {};
    DeskbotProtocol::makeUuidV4(message_id);
    StaticJsonDocument<384> doc;
    doc["protocol_version"] = "1.0";
    doc["message_id"] = message_id;
    doc["message_type"] = "audio.finish";
    doc["timestamp"] = timestamp;
    JsonObject payload = doc.createNestedObject("payload");
    payload["recording_id"] = recording_id;
    const size_t required_size = measureJson(doc);
    if (required_size == 0) {
        Serial.println("[AUDIO] ERROR: measureJson returned 0 for audio.finish.");
        return;
    }
    char output[DeskbotProtocol::kOutputBufferBytes];
    size_t len = serializeJson(doc, output, sizeof(output));
    Serial.printf("[AUDIO] TX audio.finish: required=%u capacity=%u serialized=%u\n",
                  (unsigned int)required_size,
                  (unsigned int)sizeof(output),
                  (unsigned int)len);
    if (len == 0) {
        Serial.println("[AUDIO] ERROR: serializeJson returned 0 for audio.finish.");
        return;
    }
    if (len > sizeof(output) - 1) {
        Serial.printf("[AUDIO] ERROR: JSON truncated (%u > %u bytes available).\n",
                      (unsigned int)len, (unsigned int)(sizeof(output) - 1));
        return;
    }
    output[len] = '\0';
    if (audioClient.isConnected()) {
        Serial.printf("[AUDIO] TX audio.finish (%u bytes): %s\n", (unsigned int)len, output);
        if (audioClient.sendTXT(reinterpret_cast<const uint8_t *>(output), len)) {
            Serial.println("[AUDIO] audio.finish sent.");
            audio_finish_sent = true;
        } else {
            Serial.println("[AUDIO] audio.finish send failed.");
        }
    }
}

void flushPcm16Accum() {
    if (pcm16_accum_count == 0 || !audioClient.isConnected()) return;
    const size_t bytes_to_send = pcm16_accum_count * sizeof(int16_t);
    if (!audioClient.sendBIN(reinterpret_cast<const uint8_t *>(pcm16_accum), bytes_to_send)) {
        Serial.println("[AUDIO] Binary frame send failed.");
        return;
    }
    frames_sent++;
    bytes_sent += bytes_to_send;
    pcm16_accum_count = 0;
}

void captureAndStreamPcm16() {
    const size_t bytes_read = i2s.readBytes(reinterpret_cast<char *>(rx_raw32), sizeof(rx_raw32));
    const size_t samples32 = bytes_read / sizeof(uint32_t);
    const size_t frames = samples32 / 2;
    for (size_t i = 0; i < frames && pcm16_accum_count < kPcmFrameSamples; ++i) {
        const uint32_t raw = rx_raw32[i * 2];
        const int32_t sample_24 = static_cast<int32_t>(raw) >> 8;
        const int16_t sample_16 = static_cast<int16_t>(sample_24 >> 8);
        int32_t gained = static_cast<int32_t>(sample_16 * kInputGain);
        if (gained > 32767) gained = 32767;
        if (gained < -32768) gained = -32768;
        pcm16_accum[pcm16_accum_count++] = static_cast<int16_t>(gained);
    }
    if (pcm16_accum_count >= kPcmFrameSamples) {
        flushPcm16Accum();
    }
}

void updatePushToTalkButton(uint32_t now) {
    const bool pressed = digitalRead(kPushToTalkPin) == LOW;
    if (pressed != button_raw_pressed) {
        button_raw_pressed = pressed;
        button_raw_changed_ms = now;
    }

    // Debounce: only act on stable state change.
    if (button_stable_pressed != button_raw_pressed &&
        static_cast<uint32_t>(now - button_raw_changed_ms) >= kButtonDebounceMs) {
        button_stable_pressed = button_raw_pressed;
    }

    // State machine driven by stable button state (not edge-triggered), so we
    // can retry audio.start if the WebSocket was still connecting on first press.
    if (button_stable_pressed && audio_state == AudioState::IDLE) {
        if (time(nullptr) < 1700000000) {
            // Clock not synchronized yet: transition to PENDING_START and retry
            // on each loop until the clock is ready or the button is released.
            audio_state = AudioState::PENDING_START;
            Serial.println("[AUDIO] State: PENDING_START (waiting for clock sync)");
        } else if (audio_ws_state != AudioWsState::CONNECTED) {
            connectAudioWebSocket();
        } else if (audio_ws_state == AudioWsState::CONNECTED && !audio_start_acked) {
            if (sendAudioStart()) {
                audio_state = AudioState::STREAMING;
                pcm16_accum_count = 0;
                Serial.println("[AUDIO] State: STREAMING");
            }
        }
    } else if (button_stable_pressed && audio_state == AudioState::PENDING_START) {
        // Retry sending audio.start once the clock is synchronized.
        if (time(nullptr) >= 1700000000) {
            if (audio_ws_state != AudioWsState::CONNECTED) {
                connectAudioWebSocket();
            } else if (audio_ws_state == AudioWsState::CONNECTED && !audio_start_acked) {
                if (sendAudioStart()) {
                    audio_state = AudioState::STREAMING;
                    pcm16_accum_count = 0;
                    Serial.println("[AUDIO] State: STREAMING");
                }
            }
        }
    } else if (!button_stable_pressed && audio_state == AudioState::STREAMING && !audio_finish_sent) {
        sendAudioFinish();
        audio_state = AudioState::STOPPING;
        audio_stop_deadline_ms = now + kAudioStopTimeoutMs;
        Serial.println("[AUDIO] State: STOPPING");
    } else if (!button_stable_pressed && audio_state == AudioState::PENDING_START) {
        // User released the button while waiting for clock sync: cancel.
        audio_state = AudioState::IDLE;
        Serial.println("[AUDIO] State: IDLE (start cancelled)");
    }
}

const char *audioStateName(AudioState s) {
    switch (s) {
        case AudioState::IDLE: return "IDLE";
        case AudioState::PENDING_START: return "PENDING_START";
        case AudioState::STREAMING: return "STREAMING";
        case AudioState::STOPPING: return "STOPPING";
    }
    return "UNKNOWN";
}

const char *audioWsStateName(AudioWsState s) {
    switch (s) {
        case AudioWsState::DISCONNECTED: return "DISCONNECTED";
        case AudioWsState::CONNECTING: return "CONNECTING";
        case AudioWsState::CONNECTED: return "CONNECTED";
    }
    return "UNKNOWN";
}

}  // namespace

void audioLoopBegin() {
    if (i2s_initialized) return;
    pinMode(kPushToTalkPin, INPUT_PULLUP);
    button_raw_pressed = digitalRead(kPushToTalkPin) == LOW;
    button_stable_pressed = button_raw_pressed;
    button_raw_changed_ms = millis();
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
    memset(tx_silence32, 0, sizeof(tx_silence32));
    i2s_initialized = true;
    last_report_ms = millis();
    Serial.printf("[AUDIO] I2S ready: %u Hz, 32-bit stereo.\n",
                  static_cast<unsigned int>(kSampleRate));
    Serial.println("[AUDIO] Push-to-talk on GPIO32 to stream audio.");
}

void audioLoopLoop() {
    if (!i2s_initialized) return;
    const uint32_t now = millis();
    audioClient.loop();
    updatePushToTalkButton(now);
    if (audio_state == AudioState::STREAMING && audio_ws_state == AudioWsState::CONNECTED && audio_start_acked) {
        captureAndStreamPcm16();
        i2s.write(tx_silence32, sizeof(tx_silence32));
    }
    if (audio_state == AudioState::STOPPING) {
        if (static_cast<int32_t>(now - audio_stop_deadline_ms) >= 0) {
            Serial.println("[AUDIO] Stop timeout; returning to IDLE.");
            audio_state = AudioState::IDLE;
            pcm16_accum_count = 0;
            recording_id[0] = '\0';
            audio_start_acked = false;
            audio_finish_sent = false;
        }
    }
    if (static_cast<uint32_t>(now - last_report_ms) >= kReportIntervalMs) {
        Serial.printf("[AUDIO] State=%s WS=%s acked=%d frames_sent=%lu bytes_sent=%lu heap=%lu\n",
                      audioStateName(audio_state),
                      audioWsStateName(audio_ws_state),
                      audio_start_acked ? 1 : 0,
                      static_cast<unsigned long>(frames_sent),
                      static_cast<unsigned long>(bytes_sent),
                      static_cast<unsigned long>(ESP.getFreeHeap()));
        last_report_ms = now;
    }
}

void audioLoopEnd() {
    if (!i2s_initialized) return;
    disconnectAudioWebSocket();
    i2s.end();
    i2s_initialized = false;
}

}  // namespace DeskbotAudio
