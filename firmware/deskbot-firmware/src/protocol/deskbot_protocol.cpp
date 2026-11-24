#include "deskbot_protocol.h"

#include <ArduinoJson.h>
#include <esp_system.h>
#include <time.h>

#include "log.h"

namespace DeskbotProtocol {
namespace {

constexpr char kProtocolVersion[] = "1.0";
constexpr char kHardwareModel[] = "ESP32-WROOM-32 DevKit V1";
constexpr size_t kJsonDocumentCapacity = 1536;

bool isHex(char value) {
  return (value >= '0' && value <= '9') || (value >= 'a' && value <= 'f') ||
         (value >= 'A' && value <= 'F');
}

bool isDigit(char value) { return value >= '0' && value <= '9'; }

bool isUtcTimestamp(const char *value) {
  if (value == nullptr) return false;
  const size_t length = strlen(value);
  if (length != 20 && (length < 22 || length > 30)) return false;
  if (value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
      value[13] != ':' || value[16] != ':' || value[length - 1] != 'Z') {
    return false;
  }
  for (size_t i = 0; i < 19; ++i) {
    if (i == 4 || i == 7 || i == 10 || i == 13 || i == 16) continue;
    if (!isDigit(value[i])) return false;
  }
  if (length > 20) {
    if (value[19] != '.') return false;
    for (size_t i = 20; i < length - 1; ++i) {
      if (!isDigit(value[i])) return false;
    }
  }
  const int year = (value[0] - '0') * 1000 + (value[1] - '0') * 100 +
                   (value[2] - '0') * 10 + value[3] - '0';
  const int month = (value[5] - '0') * 10 + value[6] - '0';
  const int day = (value[8] - '0') * 10 + value[9] - '0';
  const int hour = (value[11] - '0') * 10 + value[12] - '0';
  const int minute = (value[14] - '0') * 10 + value[15] - '0';
  const int second = (value[17] - '0') * 10 + value[18] - '0';
  if (year < 1 || month < 1 || month > 12) return false;
  static constexpr uint8_t daysInMonth[] = {31, 28, 31, 30, 31, 30,
                                             31, 31, 30, 31, 30, 31};
  uint8_t maxDay = daysInMonth[month - 1];
  if (month == 2 && (year % 400 == 0 || (year % 4 == 0 && year % 100 != 0))) {
    maxDay = 29;
  }
  return day >= 1 && day <= maxDay && hour <= 23 &&
         minute <= 59 && second <= 59;
}

template <size_t Capacity>
bool finishMessage(StaticJsonDocument<Capacity> &document, char *output,
                   size_t outputCapacity) {
  if (output == nullptr || outputCapacity == 0) return false;
  const size_t needed = measureJson(document);
  if (needed == 0 || needed >= outputCapacity || needed > kMaximumMessageBytes) {
    return false;
  }
  return serializeJson(document, output, outputCapacity) == needed;
}

template <size_t Capacity>
bool setEnvelope(StaticJsonDocument<Capacity> &document, const char *kind,
                 char messageId[37]) {
  char timestamp[32] = {};
  if (!makeUuidV4(messageId) || !formatUtcTimestamp(timestamp, sizeof(timestamp))) {
    return false;
  }
  document["protocol_version"] = kProtocolVersion;
  document["message_id"] = messageId;
  document["message_type"] = kind;
  document["timestamp"] = timestamp;
  document.createNestedObject("payload");
  return true;
}

bool exactKeys(JsonObjectConst object, const char *const *keys, size_t count) {
  if (object.size() != count) return false;
  for (size_t i = 0; i < count; ++i) {
    if (!object.containsKey(keys[i])) return false;
  }
  return true;
}

bool copyString(JsonVariantConst source, char *destination, size_t capacity,
                size_t minLength, size_t maxLength) {
  if (!source.is<const char *>()) return false;
  const char *text = source.as<const char *>();
  const size_t length = strlen(text);
  if (length < minLength || length > maxLength || length >= capacity) return false;
  memcpy(destination, text, length + 1);
  return true;
}

}  // namespace

bool isUuidV4(const char *value) {
  if (value == nullptr || strlen(value) != 36) return false;
  for (size_t i = 0; i < 36; ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (value[i] != '-') return false;
    } else if (!isHex(value[i])) {
      return false;
    }
  }
  const char version = value[14];
  const char variant = value[19];
  return version == '4' && (variant == '8' || variant == '9' ||
                            variant == 'a' || variant == 'A' ||
                            variant == 'b' || variant == 'B');
}

bool isBearerTokenValid(const char *value) {
  if (value == nullptr || strlen(value) != 64) return false;
  for (size_t i = 0; i < 64; ++i) {
    if (!isHex(value[i])) return false;
  }
  return true;
}

bool formatUtcTimestamp(char *output, size_t capacity) {
  if (output == nullptr || capacity < 25) return false;
  const time_t epoch = time(nullptr);
  if (epoch < 1700000000) return false;  // Require a usable SNTP-synchronized clock.
  struct tm utc = {};
  if (gmtime_r(&epoch, &utc) == nullptr) return false;
  const int milliseconds = static_cast<int>(millis() % 1000);
  const int written = snprintf(output, capacity,
                               "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                               utc.tm_year + 1900, utc.tm_mon + 1, utc.tm_mday,
                               utc.tm_hour, utc.tm_min, utc.tm_sec, milliseconds);
  return written == 24;
}

bool makeUuidV4(char output[37]) {
  if (output == nullptr) return false;
  uint8_t bytes[16] = {};
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = static_cast<uint8_t>((bytes[6] & 0x0F) | 0x40);
  bytes[8] = static_cast<uint8_t>((bytes[8] & 0x3F) | 0x80);
  const int written = snprintf(
      output, 37,
      "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
      bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6],
      bytes[7], bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13],
      bytes[14], bytes[15]);
  return written == 36;
}

bool buildRegistration(const char *deviceId, const char *bootId,
                       const char *firmwareVersion, uint32_t uptimeMs,
                       char *output, size_t capacity, char messageId[37]) {
  if (!isUuidV4(deviceId) || !isUuidV4(bootId) || firmwareVersion == nullptr ||
      strlen(firmwareVersion) == 0 || strlen(firmwareVersion) > 32) {
    return false;
  }
  StaticJsonDocument<kJsonDocumentCapacity> document;
  if (!setEnvelope(document, "device.register", messageId)) return false;
  JsonObject payload = document["payload"].as<JsonObject>();
  payload["device_id"] = deviceId;
  payload["boot_id"] = bootId;
  payload["hardware_model"] = kHardwareModel;
  payload["firmware_version"] = firmwareVersion;
  payload["uptime_ms"] = uptimeMs;
  payload.createNestedArray("capabilities");  // No peripherals are implemented yet.
  return finishMessage(document, output, capacity);
}

bool buildHeartbeat(const char *bootId, uint32_t sequence, uint32_t uptimeMs,
                    char *output, size_t capacity, char messageId[37]) {
  if (!isUuidV4(bootId) || sequence == 0) return false;
  StaticJsonDocument<kJsonDocumentCapacity> document;
  if (!setEnvelope(document, "device.heartbeat", messageId)) return false;
  JsonObject payload = document["payload"].as<JsonObject>();
  payload["boot_id"] = bootId;
  payload["sequence"] = sequence;
  payload["uptime_ms"] = uptimeMs;
  return finishMessage(document, output, capacity);
}

bool buildStatus(const char *bootId, uint32_t sequence, int wifiRssiDbm,
                 uint32_t freeHeapBytes, char *output, size_t capacity,
                 char messageId[37]) {
  if (!isUuidV4(bootId) || sequence == 0 || wifiRssiDbm < -127 ||
      wifiRssiDbm > 0) {
    return false;
  }
  StaticJsonDocument<kJsonDocumentCapacity> document;
  if (!setEnvelope(document, "device.status", messageId)) return false;
  JsonObject payload = document["payload"].as<JsonObject>();
  payload["boot_id"] = bootId;
  payload["sequence"] = sequence;
  payload["state"] = "idle";
  payload["wifi_rssi_dbm"] = wifiRssiDbm;
  payload["free_heap_bytes"] = freeHeapBytes;
  payload["last_command"] = nullptr;
  return finishMessage(document, output, capacity);
}

bool buildPong(const char *replyTo, const char *nonce, char *output,
               size_t capacity) {
  if (!isUuidV4(replyTo) || !isUuidV4(nonce)) return false;
  char messageId[37] = {};
  StaticJsonDocument<kJsonDocumentCapacity> document;
  if (!setEnvelope(document, "server.pong", messageId)) return false;
  JsonObject payload = document["payload"].as<JsonObject>();
  payload["reply_to"] = replyTo;
  payload["nonce"] = nonce;
  return finishMessage(document, output, capacity);
}

bool parseServerMessage(char *input, size_t length, const char *deviceId,
                         const char *expectedHeartbeatId,
                         uint32_t expectedHeartbeatSequence,
                         ServerMessage &message) {
  message = ServerMessage{};
  if (input == nullptr || length == 0 || length > kMaximumMessageBytes ||
      deviceId == nullptr) {
    return false;
  }

  LOG_DEBUG("WS", "Parsing server message: len=%u free_heap=%lu", (unsigned int)length, (unsigned long)ESP.getFreeHeap());

  StaticJsonDocument<kJsonDocumentCapacity> document;
  const DeserializationError error = deserializeJson(
      document, input, length, DeserializationOption::NestingLimit(8));
  if (error) {
      LOG_ERROR("WS", "JSON parse failed: %s (len=%u, free_heap=%lu)", error.c_str(), (unsigned int)length, (unsigned long)ESP.getFreeHeap());
      return false;
  }
  if (!document.is<JsonObject>()) {
      LOG_ERROR("WS", "Root is not object (len=%u)", (unsigned int)length);
      return false;
  }

  LOG_DEBUG("WS", "Parsed OK: doc_size=%u free_heap=%lu", (unsigned int)document.memoryUsage(), (unsigned long)ESP.getFreeHeap());

  JsonObjectConst envelope = document.as<JsonObjectConst>();
  static constexpr const char *envelopeKeys[] = {
      "protocol_version", "message_id", "message_type", "timestamp", "payload"};
  if (!exactKeys(envelope, envelopeKeys, 5) ||
      !envelope["protocol_version"].is<const char *>() ||
      strcmp(envelope["protocol_version"].as<const char *>(), kProtocolVersion) != 0 ||
      !copyString(envelope["message_id"], message.messageId,
                  sizeof(message.messageId), 36, 36) ||
      !isUuidV4(message.messageId) || !envelope["message_type"].is<const char *>() ||
      !envelope["timestamp"].is<const char *>() ||
      !isUtcTimestamp(envelope["timestamp"].as<const char *>()) ||
      !envelope["payload"].is<JsonObjectConst>()) {
    return false;
  }

  const char *kind = envelope["message_type"].as<const char *>();
  JsonObjectConst payload = envelope["payload"].as<JsonObjectConst>();
  if (strcmp(kind, "device.registered") == 0) {
    static constexpr const char *payloadKeys[] = {
        "accepted", "device_id", "session_id", "server_time",
        "heartbeat_interval_s", "heartbeat_timeout_s", "max_message_bytes"};
    if (!exactKeys(payload, payloadKeys, 7) ||
        !payload["accepted"].is<bool>() || !payload["accepted"].as<bool>() ||
        !payload["device_id"].is<const char *>() ||
        strcasecmp(payload["device_id"].as<const char *>(), deviceId) != 0 ||
        !payload["session_id"].is<const char *>() ||
        !isUuidV4(payload["session_id"].as<const char *>()) ||
        !payload["server_time"].is<const char *>() ||
        !isUtcTimestamp(payload["server_time"].as<const char *>()) ||
        !payload["heartbeat_interval_s"].is<uint16_t>() ||
        !payload["heartbeat_timeout_s"].is<uint16_t>() ||
        !payload["max_message_bytes"].is<uint16_t>()) {
      return false;
    }
    const uint16_t interval = payload["heartbeat_interval_s"].as<uint16_t>();
    const uint16_t timeout = payload["heartbeat_timeout_s"].as<uint16_t>();
    const uint16_t maxMessage = payload["max_message_bytes"].as<uint16_t>();
    if (interval < 1 || interval > 300 || timeout < 2 || timeout > 900 ||
        maxMessage < 256 || maxMessage > kMaximumMessageBytes) {
      return false;
    }
    message.kind = ServerMessageKind::Registered;
    message.heartbeatIntervalSeconds = interval;
    return true;
  }

  if (strcmp(kind, "device.heartbeat.ack") == 0) {
    static constexpr const char *payloadKeys[] = {"reply_to", "sequence", "server_time"};
    if (!exactKeys(payload, payloadKeys, 3) || expectedHeartbeatId == nullptr ||
        !payload["reply_to"].is<const char *>() ||
        strcmp(payload["reply_to"].as<const char *>(), expectedHeartbeatId) != 0 ||
        !payload["sequence"].is<uint32_t>() ||
        payload["sequence"].as<uint32_t>() != expectedHeartbeatSequence ||
        !payload["server_time"].is<const char *>() ||
        !isUtcTimestamp(payload["server_time"].as<const char *>())) {
      return false;
    }
    message.kind = ServerMessageKind::HeartbeatAck;
    message.sequence = expectedHeartbeatSequence;
    memcpy(message.replyTo, expectedHeartbeatId, sizeof(message.replyTo));
    return true;
  }

  if (strcmp(kind, "server.ping") == 0) {
    static constexpr const char *payloadKeys[] = {"nonce"};
    if (!exactKeys(payload, payloadKeys, 1) || !payload["nonce"].is<const char *>() ||
        !copyString(payload["nonce"], message.nonce, sizeof(message.nonce), 36, 36) ||
        !isUuidV4(message.nonce)) {
      return false;
    }
    message.kind = ServerMessageKind::Ping;
    return true;
  }

  return false;  // Commands and device-to-server message types aren't acted upon.
}

}  // namespace DeskbotProtocol
