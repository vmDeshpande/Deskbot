#include "device_websocket.h"

#include <WebSocketsClient.h>
#include <WiFi.h>
#include <esp_system.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <string.h>

#include "../protocol/deskbot_protocol.h"
#include "time_sync.h"
#include "secrets.h"

namespace DeskbotWebSocket {
namespace {

constexpr uint32_t kInitialBackoffMs = 1000;
constexpr uint32_t kMaximumBackoffMs = 60000;
constexpr uint32_t kHeartbeatAckTimeoutMs = 15000;
constexpr uint32_t kStatusIntervalMs = 60000;
constexpr uint32_t kStableRegistrationMs = 60000;
constexpr size_t kDisconnectReasonBufferSize = 64;

enum class ConnectionState : uint8_t {
  ConfigurationInvalid,
  WaitingForWiFi,
  WaitingForClock,
  Backoff,
  Connecting,
  TransportConnected,
  AwaitingRegistration,
  Registered,
};

WebSocketsClient client;
ConnectionState connectionState = ConnectionState::ConfigurationInvalid;
char bootId[37] = {};
char pendingHeartbeatId[37] = {};
uint32_t heartbeatSequence = 0;
uint32_t statusSequence = 0;
uint32_t heartbeatIntervalMs = 30000;
uint32_t retryAtMs = 0;
uint32_t heartbeatAtMs = 0;
uint32_t statusAtMs = 0;
uint32_t heartbeatAckDeadlineMs = 0;
uint32_t registrationDeadlineMs = 0;
uint32_t registeredAtMs = 0;
uint8_t backoffStep = 0;
bool configured = false;
bool transportConnected = false;
bool disconnectedActiveSession = false;
bool registrationSent = false;
bool registered = false;
bool waitingForHeartbeatAck = false;
bool receivedPending = false;
bool protocolFault = false;
bool bootIdReady = false;
bool connectedCallbackEverReached = false;
uint32_t connectionAttemptCount = 0;
size_t receiveLength = 0;
char receiveBuffer[DeskbotProtocol::kMaximumMessageBytes + 1] = {};

bool isThreeDigitStatus(const char *text, size_t length, const char *prefix) {
  const size_t prefixLength = strlen(prefix);
  if (length != prefixLength + 3 || strncmp(text, prefix, prefixLength) != 0) {
    return false;
  }
  for (size_t i = prefixLength; i < length; ++i) {
    if (text[i] < '0' || text[i] > '9') return false;
  }
  return true;
}

void formatSafeDisconnectReason(const uint8_t *payload, size_t length,
                                char *output, size_t outputCapacity) {
  if (output == nullptr || outputCapacity == 0) return;
  if (payload == nullptr || length == 0) {
    snprintf(output, outputCapacity, "none");
    return;
  }

  char sanitized[kDisconnectReasonBufferSize] = {};
  const size_t boundedLength =
      (length < sizeof(sanitized) - 1) ? length : sizeof(sanitized) - 1;
  for (size_t i = 0; i < boundedLength; ++i) {
    const uint8_t value = payload[i];
    sanitized[i] = (value >= 0x20 && value <= 0x7e) ? static_cast<char>(value) : '?';
  }

  const bool knownReason =
      strcmp(sanitized, "Connection lost") == 0 ||
      strcmp(sanitized, "TCP connection cleanup") == 0 ||
      strcmp(sanitized, "Header response timeout") == 0 ||
      isThreeDigitStatus(sanitized, boundedLength, "HTTP ") ||
      strcmp(sanitized, "WebSocket handshake failed") == 0 ||
      (boundedLength > strlen("WebSocket handshake failed - ") &&
       isThreeDigitStatus(sanitized, boundedLength,
                          "WebSocket handshake failed - HTTP "));

  if (knownReason && length == boundedLength) {
    snprintf(output, outputCapacity, "%s", sanitized);
  } else {
    snprintf(output, outputCapacity, "unrecognized");
  }
}

bool timeReached(uint32_t now, uint32_t target) {
  return static_cast<int32_t>(now - target) >= 0;
}

uint32_t nextBackoffDelayMs() {
  uint32_t base = kInitialBackoffMs;
  for (uint8_t i = 0; i < backoffStep && base < kMaximumBackoffMs; ++i) {
    base = (base > kMaximumBackoffMs / 2) ? kMaximumBackoffMs : base * 2;
  }
  if (backoffStep < 16) ++backoffStep;
  const uint32_t minimum = base / 2UL;
  const uint32_t jitterWidth = base - minimum + 1UL;
  return minimum + (esp_random() % jitterWidth);  // 50%-100%, never above the cap.
}

void scheduleReconnect(uint32_t now) {
  const uint32_t delayMs = nextBackoffDelayMs();
  retryAtMs = now + delayMs;
  connectionState = ConnectionState::Backoff;
  Serial.printf("[WS] Reconnect scheduled: delay_ms=%lu backoff=jittered\n",
                static_cast<unsigned long>(delayMs));
}

bool sendBuffer(const char *buffer, size_t length) {
  if (buffer == nullptr || length == 0 || length > DeskbotProtocol::kMaximumMessageBytes ||
      !client.isConnected()) {
    return false;
  }
  return client.sendTXT(reinterpret_cast<const uint8_t *>(buffer), length);
}

bool sendRegistration() {
  char output[DeskbotProtocol::kOutputBufferBytes] = {};
  char messageId[37] = {};
  if (!DeskbotProtocol::buildRegistration(
          DESKBOT_DEVICE_ID, bootId, "0.2.0", millis(), output, sizeof(output), messageId)) {
    return false;
  }
  if (!sendBuffer(output, strlen(output))) return false;
  registrationSent = true;
  connectionState = ConnectionState::AwaitingRegistration;
  registrationDeadlineMs = millis() + 8000UL;
  Serial.println("[WS] Registration sent; awaiting acknowledgement (timeout_ms=8000).");
  return true;
}

bool sendHeartbeat(uint32_t now) {
  if (heartbeatSequence == DeskbotProtocol::kMaximumSequence) {
    Serial.println("[WS] Heartbeat sequence exhausted; reboot required.");
    client.disconnect();
    return false;
  }
  const uint32_t nextSequence = heartbeatSequence + 1;
  char output[DeskbotProtocol::kOutputBufferBytes] = {};
  char messageId[37] = {};
  if (!DeskbotProtocol::buildHeartbeat(bootId, nextSequence, millis(), output,
                                       sizeof(output), messageId) ||
      !sendBuffer(output, strlen(output))) {
    client.disconnect();
    return false;
  }
  heartbeatSequence = nextSequence;
  memcpy(pendingHeartbeatId, messageId, sizeof(pendingHeartbeatId));
  waitingForHeartbeatAck = true;
  heartbeatAckDeadlineMs = now + kHeartbeatAckTimeoutMs;
  heartbeatAtMs = now + heartbeatIntervalMs;
  Serial.printf("[WS] Heartbeat sent: sequence=%lu\n",
                static_cast<unsigned long>(heartbeatSequence));
  return true;
}

bool sendStatus(uint32_t now) {
  if (statusSequence == DeskbotProtocol::kMaximumSequence) {
    Serial.println("[WS] Status sequence exhausted; reboot required.");
    client.disconnect();
    return false;
  }
  const uint32_t nextSequence = statusSequence + 1;
  char output[DeskbotProtocol::kOutputBufferBytes] = {};
  char messageId[37] = {};
  if (!DeskbotProtocol::buildStatus(bootId, nextSequence, WiFi.RSSI(),
                                    ESP.getFreeHeap(), output, sizeof(output),
                                    messageId) ||
      !sendBuffer(output, strlen(output))) {
    client.disconnect();
    return false;
  }
  statusSequence = nextSequence;
  statusAtMs = now + kStatusIntervalMs;
  return true;
}

bool sendPong(const DeskbotProtocol::ServerMessage &message) {
  char output[DeskbotProtocol::kOutputBufferBytes] = {};
  if (!DeskbotProtocol::buildPong(message.messageId, message.nonce, output,
                                  sizeof(output))) {
    return false;
  }
  return sendBuffer(output, strlen(output));
}

void handleIncomingMessage(uint32_t now) {
  if (!receivedPending) return;
  receivedPending = false;

  DeskbotProtocol::ServerMessage message;
  const char *expectedId = waitingForHeartbeatAck ? pendingHeartbeatId : nullptr;
  const uint32_t expectedSequence = waitingForHeartbeatAck ? heartbeatSequence : 0;
  if (!DeskbotProtocol::parseServerMessage(receiveBuffer, receiveLength,
                                           DESKBOT_DEVICE_ID, expectedId,
                                           expectedSequence, message)) {
    Serial.println("[WS] Invalid or unsupported server message; closing session.");
    protocolFault = true;
    return;
  }

  if (message.kind == DeskbotProtocol::ServerMessageKind::Registered) {
    if (!registrationSent || registered) {
      Serial.println("[WS] Unexpected registration acknowledgement; closing session.");
      protocolFault = true;
      return;
    }
    Serial.println("[WS] Registration acknowledgement received.");
    registered = true;
    connectionState = ConnectionState::Registered;
    registeredAtMs = now;
    heartbeatIntervalMs = static_cast<uint32_t>(message.heartbeatIntervalSeconds) * 1000UL;
    heartbeatAtMs = now;  // First heartbeat is sent immediately after registration.
    statusAtMs = now;     // Report the initial idle state only after registration.
    Serial.println("[WS] Device registered with backend.");
    return;
  }

  if (!registered) {
    Serial.println("[WS] Unexpected server message before registration; closing session.");
    protocolFault = true;
    return;
  }
  if (message.kind == DeskbotProtocol::ServerMessageKind::HeartbeatAck) {
    Serial.printf("[WS] Heartbeat acknowledgement received: sequence=%lu\n",
                  static_cast<unsigned long>(message.sequence));
    waitingForHeartbeatAck = false;
    return;
  }
  if (message.kind == DeskbotProtocol::ServerMessageKind::Ping) {
    Serial.println("[WS] Server application ping received.");
    if (!sendPong(message)) {
      Serial.println("[WS] Server ping response failed; closing session.");
      protocolFault = true;
    } else {
      Serial.println("[WS] Server application pong sent.");
    }
  }
}

void handleLibraryEvent(WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      connectedCallbackEverReached = true;
      transportConnected = true;
      connectionState = ConnectionState::TransportConnected;
      registrationSent = false;
      registered = false;
      waitingForHeartbeatAck = false;
      Serial.printf("[WS] Transport connected: attempt=%lu connected_callback_ever_reached=true; sending registration next.\n",
                    static_cast<unsigned long>(connectionAttemptCount));
      break;

    case WStype_DISCONNECTED: {
      char safeReason[kDisconnectReasonBufferSize] = {};
      formatSafeDisconnectReason(payload, length, safeReason, sizeof(safeReason));
      Serial.printf(
          "[WS] Transport disconnected: event=WStype_DISCONNECTED(%d) "
          "transport_connected=%s registered=%s registration_pending=%s "
          "heartbeat_pending=%s attempt=%lu connected_callback_ever_reached=%s "
          "reason=%s payload_len=%lu\n",
          static_cast<int>(type), transportConnected ? "true" : "false",
          registered ? "true" : "false",
          (registrationSent && !registered) ? "true" : "false",
          waitingForHeartbeatAck ? "true" : "false",
          static_cast<unsigned long>(connectionAttemptCount),
          connectedCallbackEverReached ? "true" : "false", safeReason,
          static_cast<unsigned long>(length));
      if (transportConnected) disconnectedActiveSession = true;
      transportConnected = false;
      connectionState = ConnectionState::Backoff;
      registrationSent = false;
      registered = false;
      waitingForHeartbeatAck = false;
      receivedPending = false;
      break;
    }

    case WStype_TEXT:
      if (payload == nullptr || length == 0 ||
          length > DeskbotProtocol::kMaximumMessageBytes || receivedPending) {
        Serial.println("[WS] Invalid or unexpected text frame; closing session.");
        protocolFault = true;
        break;
      }
      memcpy(receiveBuffer, payload, length);
      receiveBuffer[length] = '\0';
      receiveLength = length;
      receivedPending = true;
      break;

    case WStype_BIN:
    case WStype_FRAGMENT_TEXT_START:
    case WStype_FRAGMENT_BIN_START:
    case WStype_FRAGMENT:
    case WStype_FRAGMENT_FIN:
      Serial.printf("[WS] Invalid or unsupported WebSocket frame: event=%d; closing session.\n",
                    static_cast<int>(type));
      protocolFault = true;
      break;

    case WStype_ERROR:
      Serial.printf(
          "[WS] WebSocket error event: event=WStype_ERROR(%d) payload_len=%lu.\n",
          static_cast<int>(type), static_cast<unsigned long>(length));
      break;

    case WStype_PING:
    case WStype_PONG:
    default:
      // The library handles RFC 6455 control frames. Application ping is JSON.
      break;
  }
}

void websocketWorker(void *) {
  for (;;) {
    const bool wifiConnected = WiFi.status() == WL_CONNECTED;
    DeskbotTime::loop(wifiConnected);
    DeskbotWebSocket::loop(wifiConnected, DeskbotTime::isSynchronized());
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

}  // namespace

void begin() {
  if (strlen(DESKBOT_WS_HOST) == 0 || strcmp(DESKBOT_WS_HOST, "WINDOWS_LAN_IP") == 0 ||
      DESKBOT_WS_PORT == 0 || DESKBOT_WS_PATH[0] != '/' ||
      !DeskbotProtocol::isUuidV4(DESKBOT_DEVICE_ID) ||
      !DeskbotProtocol::isBearerTokenValid(DESKBOT_DEVICE_TOKEN)) {
    Serial.println("[WS] Configuration incomplete; set host, device UUID, and token in ignored secrets.h.");
    return;
  }

  client.begin(DESKBOT_WS_HOST, DESKBOT_WS_PORT, DESKBOT_WS_PATH, "deskbot.v1");
  String authorization = "Bearer ";
  authorization += DESKBOT_DEVICE_TOKEN;
  client.setAuthorization(authorization.c_str());
  // WebSocketsClient retries whenever loop() is called while disconnected. This
  // module gates those calls and is the only owner of exponential backoff/jitter.
  client.setReconnectInterval(0);
  client.enableHeartbeat(20000, 20000, 1);
  client.onEvent(handleLibraryEvent);
  configured = true;
  connectionState = ConnectionState::WaitingForWiFi;
  const BaseType_t taskCreated = xTaskCreatePinnedToCore(
      websocketWorker, "deskbot-ws", 8192, nullptr, 1, nullptr, 1);
  if (taskCreated != pdPASS) {
    configured = false;
    connectionState = ConnectionState::ConfigurationInvalid;
    Serial.println("[WS] Could not start the network worker task.");
    return;
  }
  Serial.println("[WS] Client configured for deskbot.v1.");
}

void loop(bool wifiConnected, bool clockSynchronized) {
  if (!configured || connectionState == ConnectionState::ConfigurationInvalid) return;
  const uint32_t now = millis();

  if (!wifiConnected) {
    if (!client.isConnected()) connectionState = ConnectionState::WaitingForWiFi;
    if (client.isConnected()) client.disconnect();
    if (disconnectedActiveSession) {
      disconnectedActiveSession = false;
      scheduleReconnect(now);
    }
    return;
  }
  if (!bootIdReady) {
    bootIdReady = DeskbotProtocol::makeUuidV4(bootId);
    if (!bootIdReady) {
      Serial.println("[WS] Could not create boot identity.");
      return;
    }
  }
  if (!clockSynchronized) {
    if (!client.isConnected()) connectionState = ConnectionState::WaitingForClock;
    if (client.isConnected()) client.loop();
    return;
  }

  if (disconnectedActiveSession) {
    disconnectedActiveSession = false;
    scheduleReconnect(now);
    return;
  }

  // Service the WebSocket client every iteration while WiFi+clock are ready.
  // This drives the HTTP handshake parser (which runs incrementally per call)
  // and processes WebSocket frames once the handshake completes.
  client.loop();

  // Initiate a new connection attempt only when the backoff schedule permits.
  // The library's reconnect interval is set to 0, so it will not auto-reconnect;
  // we control attempts here.
  if (!client.isConnected() && timeReached(now, retryAtMs)) {
    connectionState = ConnectionState::Connecting;
    retryAtMs = now + nextBackoffDelayMs();
    ++connectionAttemptCount;
    Serial.printf("[WS] Starting connection attempt=%lu.\n",
                  static_cast<unsigned long>(connectionAttemptCount));
    // client.loop() already called above; it will initiate the TCP connection.
  } else if (!client.isConnected()) {
    connectionState = ConnectionState::Backoff;
  }

  if (disconnectedActiveSession) {
    disconnectedActiveSession = false;
    scheduleReconnect(millis());
    return;
  }

  const uint32_t afterClientLoop = millis();
  if (protocolFault) {
    protocolFault = false;
    Serial.println("[WS] Invalid or unsupported server message; closing session.");
    client.disconnect();
    return;
  }
  handleIncomingMessage(afterClientLoop);
  if (!client.isConnected()) return;

  if (registered && backoffStep > 0 &&
      static_cast<uint32_t>(afterClientLoop - registeredAtMs) >= kStableRegistrationMs) {
    backoffStep = 0;
  }

  if (registered && waitingForHeartbeatAck &&
      timeReached(afterClientLoop, heartbeatAckDeadlineMs)) {
    Serial.println("[WS] Heartbeat acknowledgement timed out; reconnecting.");
    client.disconnect();
    return;
  }

  if (registrationSent && !registered &&
      timeReached(afterClientLoop, registrationDeadlineMs)) {
    Serial.println("[WS] Registration acknowledgement timeout; reconnecting.");
    client.disconnect();
    return;
  }

  // Send registration only after the WebSocket handshake has completed
  // (signaled by WStype_CONNECTED -> transportConnected == true).
  if (client.isConnected() && transportConnected && !registrationSent) {
    if (!sendRegistration()) {
      Serial.println("[WS] Registration send failed.");
      client.disconnect();
      return;
    }
  }

  if (!registered) return;
  if (!waitingForHeartbeatAck && timeReached(afterClientLoop, heartbeatAtMs)) {
    if (!sendHeartbeat(afterClientLoop)) return;
  }
  if (timeReached(afterClientLoop, statusAtMs)) sendStatus(afterClientLoop);
}

bool isRegistered() { return registered; }

}  // namespace DeskbotWebSocket
