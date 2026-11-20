#pragma once

#include <Arduino.h>
#include <stdint.h>

namespace DeskbotProtocol {

constexpr size_t kMaximumMessageBytes = 4096;
constexpr size_t kOutputBufferBytes = 512;
constexpr uint32_t kMaximumSequence = 0xFFFFFFFFUL;

enum class ServerMessageKind : uint8_t {
  Invalid,
  Registered,
  HeartbeatAck,
  Ping,
};

struct ServerMessage {
  ServerMessageKind kind = ServerMessageKind::Invalid;
  char messageId[37] = {};
  char replyTo[37] = {};
  char nonce[37] = {};
  uint32_t sequence = 0;
  uint16_t heartbeatIntervalSeconds = 30;
};

bool isUuidV4(const char *value);
bool isBearerTokenValid(const char *value);
bool formatUtcTimestamp(char *output, size_t capacity);
bool makeUuidV4(char output[37]);

bool buildRegistration(const char *deviceId, const char *bootId,
                       const char *firmwareVersion, uint32_t uptimeMs,
                       char *output, size_t capacity, char messageId[37]);
bool buildHeartbeat(const char *bootId, uint32_t sequence, uint32_t uptimeMs,
                    char *output, size_t capacity, char messageId[37]);
bool buildStatus(const char *bootId, uint32_t sequence, int wifiRssiDbm,
                 uint32_t freeHeapBytes, char *output, size_t capacity,
                 char messageId[37]);
bool buildPong(const char *replyTo, const char *nonce, char *output,
               size_t capacity);

bool parseServerMessage(char *input, size_t length, const char *deviceId,
                        const char *expectedHeartbeatId,
                        uint32_t expectedHeartbeatSequence,
                        ServerMessage &message);

}  // namespace DeskbotProtocol
