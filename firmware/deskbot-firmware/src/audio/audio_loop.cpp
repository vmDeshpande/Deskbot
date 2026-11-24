#include "audio_loop.h"

#include <ESP_I2S.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <esp_system.h>
#include <time.h>

#include "../protocol/deskbot_protocol.h"
#include "secrets.h"
#include "log.h"

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

// TTS playback constants
constexpr size_t kPlaybackBufferFrames = 2048;  // ~64ms buffer at 16kHz stereo
constexpr size_t kPlaybackChunkSamples = 320;   // Samples per chunk (mono, 20ms)

enum class AudioState : uint8_t {
    IDLE,
    PENDING_START,   // Waiting for clock sync before sending audio.start
    STREAMING,
    STOPPING,
    // TTS playback states
    PLAYBACK_STARTING,  // Received tts.start, preparing for playback
    PLAYBACK_STREAMING, // Actively playing audio chunks
    PLAYBACK_STOPPING,  // Received tts.end, draining buffer
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

// Capture buffers
uint32_t rx_raw32[kI2sReadFrames * 2];
uint32_t tx_silence32[kI2sReadFrames * 2];
int16_t pcm16_accum[kPcmFrameSamples];
size_t pcm16_accum_count = 0;

// Playback buffers
int16_t playback_buffer[kPlaybackBufferFrames];  // Mono PCM16 buffer
size_t playback_write_idx = 0;
size_t playback_read_idx = 0;
size_t playback_available = 0;
bool playback_active = false;
uint32_t playback_sample_rate = 16000;
uint8_t playback_channels = 1;
uint8_t playback_bits_per_sample = 16;

size_t frames_sent = 0;
size_t bytes_sent = 0;

bool ws_ever_connected = false;

void audioWsEvent(WStype_t type, uint8_t *payload, size_t length);
void handleAudioText(char *text, size_t length);
void handleAudioBinary(uint8_t *payload, size_t length);
bool playbackBufferWrite(const int16_t *data, size_t samples);
size_t playbackBufferRead(int16_t *output, size_t max_samples);
void playbackBufferReset();
bool isPlaybackBufferUnderrun();
bool isPlaybackBufferOverrun();
const char *audioStateName(AudioState s);

void audioWsEvent(WStype_t type, uint8_t *payload, size_t length) {
    switch (type) {
        case WStype_CONNECTED:
            audio_ws_state = AudioWsState::CONNECTED;
            if (!ws_ever_connected) {
                ws_ever_connected = true;
                LOG_INFO("AUDIO", "Connected");
            } else {
                LOG_DEBUG("AUDIO", "Reconnected");
            }
            break;
        case WStype_DISCONNECTED: {
            const char *reason = reinterpret_cast<const char *>(payload);
            if (length > 0 && reason[0] != '\0') {
                LOG_WARN("AUDIO", "Disconnected: %s", reason);
            } else {
                LOG_WARN("AUDIO", "Disconnected");
            }
            audio_ws_state = AudioWsState::DISCONNECTED;
            audio_start_acked = false;
            audio_finish_sent = false;
            if (audio_state == AudioState::STREAMING || audio_state == AudioState::STOPPING ||
                audio_state == AudioState::PLAYBACK_STREAMING || audio_state == AudioState::PLAYBACK_STOPPING) {
                audio_state = AudioState::IDLE;
                pcm16_accum_count = 0;
                recording_id[0] = '\0';
                playbackBufferReset();
                LOG_DEBUG("AUDIO", "State: IDLE (WS disconnected)");
            }
            break;
        }
        case WStype_TEXT:
            handleAudioText(reinterpret_cast<char *>(payload), length);
            break;
        case WStype_BIN:
            handleAudioBinary(payload, length);
            break;
        default:
            break;
    }
}

bool playbackBufferWrite(const int16_t *data, size_t samples) {
    if (samples == 0) return true;
    
    // Check for overrun
    size_t free_space = kPlaybackBufferFrames - playback_available;
    if (samples > free_space) {
        LOG_WARN("AUDIO", "Playback buffer overrun: need %u, free %u", (unsigned int)samples, (unsigned int)free_space);
        return false;
    }
    
    size_t first_chunk = min(samples, kPlaybackBufferFrames - playback_write_idx);
    memcpy(&playback_buffer[playback_write_idx], data, first_chunk * sizeof(int16_t));
    if (first_chunk < samples) {
        memcpy(playback_buffer, data + first_chunk, (samples - first_chunk) * sizeof(int16_t));
    }
    playback_write_idx = (playback_write_idx + samples) % kPlaybackBufferFrames;
    playback_available += samples;
    return true;
}

size_t playbackBufferRead(int16_t *output, size_t max_samples) {
    if (playback_available == 0) return 0;
    
    size_t to_read = min(max_samples, playback_available);
    size_t first_chunk = min(to_read, kPlaybackBufferFrames - playback_read_idx);
    memcpy(output, &playback_buffer[playback_read_idx], first_chunk * sizeof(int16_t));
    if (first_chunk < to_read) {
        memcpy(output + first_chunk, playback_buffer, (to_read - first_chunk) * sizeof(int16_t));
    }
    playback_read_idx = (playback_read_idx + to_read) % kPlaybackBufferFrames;
    playback_available -= to_read;
    return to_read;
}

void playbackBufferReset() {
    playback_write_idx = 0;
    playback_read_idx = 0;
    playback_available = 0;
}

bool isPlaybackBufferUnderrun() {
    // Consider underrun if less than 2 chunks available (40ms)
    return playback_available < (kPlaybackChunkSamples * 2);
}

bool isPlaybackBufferOverrun() {
    // Consider overrun if more than 80% full
    return playback_available > (kPlaybackBufferFrames * 4 / 5);
}

void handleAudioText(char *text, size_t length) {
    // JSON document capacity: 2048 bytes.
    // Calculated based on maximum expected payload sizes:
    // - audio.started: ~300 bytes
    // - audio.completed: ~200 bytes
    // - audio.transcription: ~300 bytes + transcript (max ~200)
    // - command.completed: ~500 bytes + response_text (max ~300, Devanagari = 3 bytes/char)
    // - audio.error: ~150 bytes
    // - audio.partial: ~100 bytes
    // - tts.start: ~200 bytes
    // - tts.end: ~150 bytes
    // - tts.error: ~150 bytes
    // With envelope overhead and safety margin: 2048 bytes is sufficient.
    StaticJsonDocument<2048> doc;
    
    // DEBUG: Log incoming message type and size before parsing
    LOG_DEBUG("AUDIO", "Parsing message: len=%u free_heap=%lu", (unsigned int)length, (unsigned long)ESP.getFreeHeap());
    
    DeserializationError error = deserializeJson(doc, text, length);
    if (error) {
        LOG_ERROR("AUDIO", "Failed to parse message: %s (len=%u, free_heap=%lu)", error.c_str(), (unsigned int)length, (unsigned long)ESP.getFreeHeap());
        return;
    }
    
    LOG_DEBUG("AUDIO", "Parsed OK: doc_size=%u free_heap=%lu", (unsigned int)doc.memoryUsage(), (unsigned long)ESP.getFreeHeap());
    
    const char *message_type = doc["message_type"];
    if (message_type == nullptr) return;
    if (strcmp(message_type, "audio.started") == 0) {
        const char *rid = doc["payload"]["recording_id"];
        audio_start_acked = true;
        LOG_INFO("AUDIO", "Recording started (id=%s)", rid ? rid : "unknown");
        Serial.println("\n[LISTENING] Speak now...\n");
    } else if (strcmp(message_type, "audio.completed") == 0) {
        // Silent at INFO level - details at DEBUG
        LOG_DEBUG("AUDIO", "Recording completed");
    } else if (strcmp(message_type, "audio.transcription") == 0) {
        const char *transcript = doc["payload"]["text"];
        const char *language = doc["payload"]["language"];
        Serial.printf("\n[STT] %s\n", transcript ? transcript : "");
        Serial.printf("[STT] Language: %s\n\n", language ? language : "unknown");
    } else if (strcmp(message_type, "command.completed") == 0) {
        const char *intent = doc["payload"]["intent"];
        bool recognized = doc["payload"]["recognized"] | false;
        const char *response_text = doc["payload"]["response_text"];
        if (recognized) {
            Serial.printf("[COMMAND] %s\n", intent ? intent : "unknown");
            Serial.printf("[RESPONSE] %s\n\n", response_text ? response_text : "");
            Serial.println("[STATUS] Ready for next command\n");
        } else {
            LOG_WARN("CMD", "Unrecognized: %s", response_text ? response_text : "unknown");
            Serial.println("[STATUS] Ready for next command\n");
        }
        // Reset state after command completes (playback will be handled separately)
        audio_state = AudioState::IDLE;
        pcm16_accum_count = 0;
        recording_id[0] = '\0';
        audio_start_acked = false;
        audio_finish_sent = false;
    } else if (strcmp(message_type, "audio.error") == 0) {
        const char *code = doc["payload"]["code"];
        LOG_ERROR("AUDIO", "Server error: %s", code ? code : "unknown");
        audio_state = AudioState::IDLE;
        pcm16_accum_count = 0;
        recording_id[0] = '\0';
        audio_start_acked = false;
        audio_finish_sent = false;
    } else if (strcmp(message_type, "audio.partial") == 0) {
        // Partial results at DEBUG level only
        LOG_DEBUG("AUDIO", "Partial transcript");
    } else if (strcmp(message_type, "tts.start") == 0) {
        // TTS playback starting
        if (audio_state != AudioState::IDLE) {
            LOG_WARN("AUDIO", "TTS start received in state %s, ignoring", audioStateName(audio_state));
            return;
        }
        
        const char *rid = doc["payload"]["recording_id"];
        playback_sample_rate = doc["payload"]["sample_rate"] | 16000;
        playback_channels = doc["payload"]["channels"] | 1;
        playback_bits_per_sample = doc["payload"]["bits_per_sample"] | 16;
        const char *format = doc["payload"]["format"];
        float duration = doc["payload"]["duration_seconds"] | 0.0f;
        
        if (strcmp(recording_id, rid ? rid : "") != 0) {
            LOG_WARN("AUDIO", "TTS recording_id mismatch: expected %s, got %s", recording_id, rid ? rid : "null");
        }
        
        // Validate format
        if (playback_sample_rate != 16000 || playback_channels != 1 || playback_bits_per_sample != 16) {
            LOG_ERROR("AUDIO", "Unsupported TTS format: sr=%u ch=%u bits=%u", 
                      (unsigned int)playback_sample_rate, (unsigned int)playback_channels, (unsigned int)playback_bits_per_sample);
            return;
        }
        
        playbackBufferReset();
        playback_active = true;
        audio_state = AudioState::PLAYBACK_STARTING;
        
        LOG_INFO("AUDIO", "TTS playback starting: sr=%u ch=%u bits=%u fmt=%s dur=%.2fs",
                 (unsigned int)playback_sample_rate, (unsigned int)playback_channels,
                 (unsigned int)playback_bits_per_sample, format ? format : "unknown", duration);
        Serial.printf("\n[PLAYBACK] Starting (%.2fs)...\n\n", duration);
    } else if (strcmp(message_type, "tts.end") == 0) {
        // TTS playback ending
        if (audio_state != AudioState::PLAYBACK_STREAMING && audio_state != AudioState::PLAYBACK_STARTING) {
            LOG_WARN("AUDIO", "TTS end received in state %s, ignoring", audioStateName(audio_state));
            return;
        }
        
        const char *rid = doc["payload"]["recording_id"];
        uint32_t total_bytes = doc["payload"]["total_bytes"] | 0;
        uint32_t chunks = doc["payload"]["chunks"] | 0;
        
        LOG_INFO("AUDIO", "TTS playback ended: bytes=%u chunks=%u", (unsigned int)total_bytes, (unsigned int)chunks);
        Serial.println("[PLAYBACK] Completed\n");
        
        audio_state = AudioState::PLAYBACK_STOPPING;
    } else if (strcmp(message_type, "tts.error") == 0) {
        // TTS playback error
        const char *code = doc["payload"]["code"];
        const char *message = doc["payload"]["message"];
        LOG_ERROR("AUDIO", "TTS error: %s - %s", code ? code : "unknown", message ? message : "unknown");
        Serial.printf("[PLAYBACK] Error: %s\n\n", message ? message : "unknown");
        
        playback_active = false;
        playbackBufferReset();
        audio_state = AudioState::IDLE;
    }
}

void handleAudioBinary(uint8_t *payload, size_t length) {
    // Handle incoming binary audio frames during TTS playback
    if (audio_state != AudioState::PLAYBACK_STARTING && audio_state != AudioState::PLAYBACK_STREAMING) {
        LOG_DEBUG("AUDIO", "Binary frame received in state %s, ignoring (%u bytes)", audioStateName(audio_state), (unsigned int)length);
        return;
    }
    
    if (length == 0 || length % 2 != 0) {
        LOG_WARN("AUDIO", "Invalid binary frame size: %u", (unsigned int)length);
        return;
    }
    
    const int16_t *samples = reinterpret_cast<const int16_t *>(payload);
    size_t sample_count = length / sizeof(int16_t);
    
    if (!playbackBufferWrite(samples, sample_count)) {
        LOG_WARN("AUDIO", "Failed to write %u samples to playback buffer", (unsigned int)sample_count);
        return;
    }
    
    // Transition to streaming state on first chunk
    if (audio_state == AudioState::PLAYBACK_STARTING) {
        audio_state = AudioState::PLAYBACK_STREAMING;
        LOG_DEBUG("AUDIO", "State: PLAYBACK_STREAMING");
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
    LOG_DEBUG("AUDIO", "Connecting...");
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
        LOG_ERROR("AUDIO", "Clock not synchronized; cannot send audio.start");
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
    char output[DeskbotProtocol::kOutputBufferBytes];
    size_t len = serializeJson(doc, output, sizeof(output));
    if (len == 0 || len > sizeof(output) - 1) {
        LOG_ERROR("AUDIO", "Failed to serialize audio.start");
        return false;
    }
    output[len] = '\0';
    if (audioClient.isConnected()) {
        LOG_DEBUG("AUDIO", "Sending audio.start (%u bytes)", (unsigned int)len);
        if (audioClient.sendTXT(reinterpret_cast<const uint8_t *>(output), len)) {
            return true;
        }
        LOG_ERROR("AUDIO", "audio.start send failed");
    }
    return false;
}

void sendAudioFinish() {
    if (!recording_id[0]) return;
    if (audio_finish_sent) return;
    char timestamp[32] = {};
    if (!DeskbotProtocol::formatUtcTimestamp(timestamp, sizeof(timestamp))) {
        LOG_ERROR("AUDIO", "Clock not synchronized; cannot send audio.finish");
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
    char output[DeskbotProtocol::kOutputBufferBytes];
    size_t len = serializeJson(doc, output, sizeof(output));
    if (len == 0 || len > sizeof(output) - 1) {
        LOG_ERROR("AUDIO", "Failed to serialize audio.finish");
        return;
    }
    output[len] = '\0';
    if (audioClient.isConnected()) {
        LOG_DEBUG("AUDIO", "Sending audio.finish (%u bytes)", (unsigned int)len);
        if (audioClient.sendTXT(reinterpret_cast<const uint8_t *>(output), len)) {
            audio_finish_sent = true;
        } else {
            LOG_ERROR("AUDIO", "audio.finish send failed");
        }
    }
}

void flushPcm16Accum() {
    if (pcm16_accum_count == 0 || !audioClient.isConnected()) return;
    const size_t bytes_to_send = pcm16_accum_count * sizeof(int16_t);
    if (!audioClient.sendBIN(reinterpret_cast<const uint8_t *>(pcm16_accum), bytes_to_send)) {
        LOG_DEBUG("AUDIO", "Binary frame send failed");
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

    // Ignore button presses during TTS playback
    bool is_playback = (audio_state == AudioState::PLAYBACK_STARTING ||
                       audio_state == AudioState::PLAYBACK_STREAMING ||
                       audio_state == AudioState::PLAYBACK_STOPPING);
    
    // State machine driven by stable button state (not edge-triggered), so we
    // can retry audio.start if the WebSocket was still connecting on first press.
    if (button_stable_pressed && audio_state == AudioState::IDLE && !is_playback) {
        if (time(nullptr) < 1700000000) {
            // Clock not synchronized yet: transition to PENDING_START and retry
            // on each loop until the clock is ready or the button is released.
            audio_state = AudioState::PENDING_START;
            LOG_DEBUG("AUDIO", "State: PENDING_START (waiting for clock sync)");
        } else if (audio_ws_state != AudioWsState::CONNECTED) {
            connectAudioWebSocket();
        } else if (audio_ws_state == AudioWsState::CONNECTED && !audio_start_acked) {
            if (sendAudioStart()) {
                audio_state = AudioState::STREAMING;
                pcm16_accum_count = 0;
                LOG_INFO("AUDIO", "Streaming...");
            }
        }
    } else if (button_stable_pressed && audio_state == AudioState::PENDING_START && !is_playback) {
        // Retry sending audio.start once the clock is synchronized.
        if (time(nullptr) >= 1700000000) {
            if (audio_ws_state != AudioWsState::CONNECTED) {
                connectAudioWebSocket();
            } else if (audio_ws_state == AudioWsState::CONNECTED && !audio_start_acked) {
                if (sendAudioStart()) {
                    audio_state = AudioState::STREAMING;
                    pcm16_accum_count = 0;
                    LOG_INFO("AUDIO", "Streaming...");
                }
            }
        }
    } else if (!button_stable_pressed && audio_state == AudioState::STREAMING && !audio_finish_sent && !is_playback) {
        sendAudioFinish();
        audio_state = AudioState::STOPPING;
        audio_stop_deadline_ms = now + kAudioStopTimeoutMs;
        LOG_DEBUG("AUDIO", "State: STOPPING");
    } else if (!button_stable_pressed && audio_state == AudioState::PENDING_START && !is_playback) {
        // User released the button while waiting for clock sync: cancel.
        audio_state = AudioState::IDLE;
        LOG_DEBUG("AUDIO", "State: IDLE (start cancelled)");
    }
    // Ignore button presses during playback - they will be handled after playback completes
}

const char *audioStateName(AudioState s) {
    switch (s) {
        case AudioState::IDLE: return "IDLE";
        case AudioState::PENDING_START: return "PENDING_START";
        case AudioState::STREAMING: return "STREAMING";
        case AudioState::STOPPING: return "STOPPING";
        case AudioState::PLAYBACK_STARTING: return "PLAYBACK_STARTING";
        case AudioState::PLAYBACK_STREAMING: return "PLAYBACK_STREAMING";
        case AudioState::PLAYBACK_STOPPING: return "PLAYBACK_STOPPING";
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
    LOG_INFO("AUDIO", "Initializing I2S...");
    if (!i2s.begin(I2S_MODE_STD, kSampleRate, I2S_DATA_BIT_WIDTH_32BIT,
                   I2S_SLOT_MODE_STEREO, -1)) {
        LOG_ERROR("AUDIO", "I2S begin() failed! lastError=%d", i2s.lastError());
        return;
    }
    if (!i2s.configureRX(kSampleRate, I2S_DATA_BIT_WIDTH_32BIT,
                         I2S_SLOT_MODE_STEREO, I2S_RX_TRANSFORM_NONE, -1)) {
        LOG_ERROR("AUDIO", "configureRX failed! lastError=%d", i2s.lastError());
        i2s.end();
        return;
    }
    memset(tx_silence32, 0, sizeof(tx_silence32));
    i2s_initialized = true;
    last_report_ms = millis();
    LOG_INFO("AUDIO", "Microphone ready (%u Hz)", static_cast<unsigned int>(kSampleRate));
}

void audioLoopLoop() {
    if (!i2s_initialized) return;
    const uint32_t now = millis();
    audioClient.loop();
    updatePushToTalkButton(now);
    
    // Handle capture/streaming state
    if (audio_state == AudioState::STREAMING && audio_ws_state == AudioWsState::CONNECTED && audio_start_acked) {
        captureAndStreamPcm16();
        i2s.write(tx_silence32, sizeof(tx_silence32));
    }
    
    // Handle playback states
    if (audio_state == AudioState::PLAYBACK_STARTING) {
        // Wait for first audio chunks to arrive
        // Write silence until we have data
        i2s.write(tx_silence32, sizeof(tx_silence32));
        
        // Transition to streaming once we have enough buffered data
        if (playback_available >= kPlaybackChunkSamples * 2) {
            audio_state = AudioState::PLAYBACK_STREAMING;
            LOG_DEBUG("AUDIO", "State: PLAYBACK_STREAMING (buffered %u samples)", (unsigned int)playback_available);
        }
    } else if (audio_state == AudioState::PLAYBACK_STREAMING) {
        // Read from playback buffer and write to I2S TX
        // We need to write stereo frames (left=right for mono playback)
        int16_t mono_samples[kI2sReadFrames];
        size_t samples_read = playbackBufferRead(mono_samples, kI2sReadFrames);
        
        if (samples_read > 0) {
            // Convert mono to stereo 32-bit for I2S
            for (size_t i = 0; i < samples_read; ++i) {
                int32_t sample_32 = static_cast<int32_t>(mono_samples[i]) << 8;  // 16-bit to 24-bit aligned
                uint32_t stereo_sample = (static_cast<uint32_t>(sample_32) & 0xFFFFFF00) | 
                                         ((static_cast<uint32_t>(sample_32) >> 8) & 0xFF);
                // Write same sample to both left and right channels
                tx_silence32[i * 2] = stereo_sample;      // Left channel
                tx_silence32[i * 2 + 1] = stereo_sample;  // Right channel
            }
            i2s.write(tx_silence32, samples_read * 2 * sizeof(uint32_t));
        } else {
            // Buffer underrun - write silence
            i2s.write(tx_silence32, sizeof(tx_silence32));
            if (isPlaybackBufferUnderrun()) {
                LOG_WARN("AUDIO", "Playback buffer underrun");
            }
        }
    } else if (audio_state == AudioState::PLAYBACK_STOPPING) {
        // Drain remaining buffer
        int16_t mono_samples[kI2sReadFrames];
        size_t samples_read = playbackBufferRead(mono_samples, kI2sReadFrames);
        
        if (samples_read > 0) {
            for (size_t i = 0; i < samples_read; ++i) {
                int32_t sample_32 = static_cast<int32_t>(mono_samples[i]) << 8;
                uint32_t stereo_sample = (static_cast<uint32_t>(sample_32) & 0xFFFFFF00) | 
                                         ((static_cast<uint32_t>(sample_32) >> 8) & 0xFF);
                tx_silence32[i * 2] = stereo_sample;
                tx_silence32[i * 2 + 1] = stereo_sample;
            }
            i2s.write(tx_silence32, samples_read * 2 * sizeof(uint32_t));
        } else {
            // Buffer drained, return to IDLE
            playback_active = false;
            playbackBufferReset();
            audio_state = AudioState::IDLE;
            LOG_INFO("AUDIO", "Playback completed, returning to IDLE");
            Serial.println("[STATUS] Ready for next command\n");
        }
    } else {
        // IDLE or other states - write silence to keep I2S clock running
        i2s.write(tx_silence32, sizeof(tx_silence32));
    }
    
    if (audio_state == AudioState::STOPPING) {
        if (static_cast<int32_t>(now - audio_stop_deadline_ms) >= 0) {
            LOG_WARN("AUDIO", "Stop timeout; returning to IDLE");
            audio_state = AudioState::IDLE;
            pcm16_accum_count = 0;
            recording_id[0] = '\0';
            audio_start_acked = false;
            audio_finish_sent = false;
        }
    }
    // Periodic state reporting at DEBUG level only
    if (static_cast<uint32_t>(now - last_report_ms) >= kReportIntervalMs) {
        LOG_DEBUG("AUDIO", "State=%s WS=%s acked=%d frames=%lu bytes=%lu heap=%lu pb_avail=%u",
                  audioStateName(audio_state),
                  audioWsStateName(audio_ws_state),
                  audio_start_acked ? 1 : 0,
                  static_cast<unsigned long>(frames_sent),
                  static_cast<unsigned long>(bytes_sent),
                  static_cast<unsigned long>(ESP.getFreeHeap()),
                  (unsigned int)playback_available);
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