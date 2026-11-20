# Deskbot WebSocket Protocol V1

**Status:** Backend and ESP32 client V1 implementations are present. Physical device validation is still pending.

### V1 implementation decisions

- `device.error` is device-to-server only. Server-originated protocol failures use WebSocket close codes and sanitized reasons; the server never sends `device.error`.
- Heartbeat and status messages have separate positive, monotonically increasing sequence counters, each reset on a new `boot_id`.
- The registration `device_id` must exactly match the UUID bound to the authenticated bearer token.
- Firmware uses one application-owned reconnect state machine with exponential backoff and jitter. Links2004 library reconnect attempts are gated by that state machine.
- The backend uses Uvicorn's explicit `websockets-sansio` implementation with a 4,096-byte maximum, 20-second transport ping interval and timeout, compression disabled, and one worker. This implementation does not support Uvicorn's `ws_max_queue` option; V1 instead bounds each outbound application queue to 32 messages and processes inbound ASGI frames sequentially.
- Uvicorn 0.35 or newer is required for `websockets-sansio`; the runtime requirements express this minimum. The selected implementation supports the configured maximum size, transport ping interval/timeout, and compression flag.
- Repeated heartbeat sequence numbers may be acknowledged, but they never update the valid-heartbeat timestamp. A lower sequence is rejected. The first heartbeat may be immediate; later advancing heartbeats faster than the 30-second interval minus the documented ±3-second jitter allowance are rejected.
- Session state is process-local and supported only with one worker. A stale connection removes a device entry only when it still owns the current session generation.
- For Windows development, discover the active IPv4 address with `ipconfig`; do not use the future Debian host address. The current development machine was found at `192.168.1.50`, but this can change. The ESP32 URL format is `ws://WINDOWS_LAN_IP:8000/ws/device`.
- Backend bind host, port, and WebSocket path are configurable with `DESKBOT_HOST`, `DESKBOT_PORT`, and `DESKBOT_WEBSOCKET_PATH` (defaults: `0.0.0.0`, `8000`, `/ws/device`).

This document defines a small, bounded JSON protocol for one ESP32 device connection to the Deskbot FastAPI service. It does not add audio streaming or alter the existing HTTP API.

## 1. Current architecture and scope

The backend in `server/app/main.py` creates the FastAPI app, `server/app/api/routes.py` registers the `/api/v1` HTTP routes, and `server/app/api/websocket.py` implements `/ws/device`. Protocol validation lives in `server/app/api/websocket_schemas.py`, token verification in `server/app/security/device_auth.py`, and in-memory session ownership in `server/app/services/device_sessions.py`.

The firmware uses PlatformIO, the Arduino framework, and the `esp32dev` environment in `firmware/deskbot-firmware/platformio.ini`. It implements Wi-Fi, SNTP, authenticated registration, heartbeat/status messages, server ping responses, and reconnect handling. Display, touch, microphone, speaker, and command execution remain unimplemented.

The WebSocket endpoint is:

```text
ws://WINDOWS_LAN_IP:8000/ws/device
```

Use `ws://` only for local development on an isolated, trusted LAN. Production device authentication requires confidentiality and integrity; use `wss://` with certificate validation. A bearer token over plain `ws://` can be read or replayed by anyone able to observe the network.

The API remains available on its current routes: `GET /`, `GET /api/v1/health`, `GET /api/v1/status`, and `/docs`. WebSocket and HTTP routing share the FastAPI/Uvicorn process on port 8000.

### Protocol layers

- RFC 6455 provides the connection, text/binary framing, close handshake, and transport-level Ping/Pong control frames.
- Deskbot V1 application messages are UTF-8 JSON text messages with the envelope defined below.
- WebSocket control frames are transport signals, not Deskbot application messages, so they do not use the JSON envelope.
- V1 accepts text application messages only. Binary audio frames are reserved for a future negotiated protocol version.

## 2. Envelope and validation rules

Every application-level V1 JSON message has exactly this top-level shape:

```json
{
  "protocol_version": "1.0",
  "message_id": "d5a825bb-3412-4a83-a480-80bc7b0d2a49",
  "message_type": "device.heartbeat",
  "timestamp": "2026-11-20T12:00:00Z",
  "payload": {}
}
```

| Field | Rule |
| --- | --- |
| `protocol_version` | Required string, exactly `1.0` for this protocol. A future incompatible protocol uses a new negotiated subprotocol/version. |
| `message_id` | Required UUIDv4 string, unique per sender and session. The receiver keeps a bounded recent-ID cache to suppress duplicates. |
| `message_type` | Required exact member of the V1 message list in section 3. Unknown types are rejected. |
| `timestamp` | Required RFC 3339 UTC timestamp ending in `Z`, millisecond precision recommended. It is diagnostic metadata; ordering and timeouts use server receipt time and heartbeat sequence numbers. |
| `payload` | Required JSON object. Validate required fields, types, ranges, and per-message limits before processing. |

Additional rules:

- Reject non-text application frames in V1, invalid UTF-8, invalid JSON, non-object top-level JSON, duplicate JSON keys, missing envelope keys, and unknown top-level keys.
- Validate payloads against the schema for the given `message_type`. Unknown payload keys may be ignored for additive compatibility; they must never override validated fields.
- Maximum complete text message size: **4,096 bytes**, measured as UTF-8 bytes before JSON parsing. Configure the Uvicorn WebSocket backend with a matching maximum. The firmware parser must use bounded storage.
- Limit message type and string lengths; no unbounded arrays or nested payloads. Cap `capabilities` at 16 entries and status/error strings at 256 characters.
- Use positive, bounded integers for counters and sizes; reject negative values, booleans where integers are required, NaN, and infinity.
- Device timestamps may be inaccurate before SNTP synchronization. Registration should follow Wi-Fi and best-effort SNTP sync. The server returns `server_time`; it should record receipt time separately and should not reject solely on timestamp skew.
- Receivers may keep a rolling cache of the latest 256 message IDs per session. For state-changing commands, use `command_id` as the longer-lived idempotency key.
- The direction table below is authoritative. `device.*` and `server.*` are operation names, not a guarantee about which side sends each message; for example, `device.registered` is sent by the server.

## 3. V1 application messages

| Message type | Direction | Purpose |
| --- | --- | --- |
| `device.register` | Device → server | Identify the provisioned device and its firmware/capabilities after a WebSocket upgrade. |
| `device.registered` | Server → device | Accept registration and assign the connection session and heartbeat parameters. |
| `device.heartbeat` | Device → server | Periodic liveness report with an increasing boot-local sequence. |
| `device.heartbeat.ack` | Server → device | Confirm a heartbeat and provide authoritative server time. |
| `device.status` | Device → server | Report current UI/device state and optional resource telemetry. |
| `server.ping` | Server → device | Optional application-level diagnostic ping; not required for routine liveness. |
| `server.pong` | Device → server | Reply to `server.ping`, echoing its nonce and message ID. |
| `server.command` | Server → device | Deliver one allowlisted, expiring command. |
| `device.error` | Device → server | Report a protocol or command error without exposing internals or credentials. |

### 3.1 `device.register`

Sent once after the WebSocket handshake. `device_id` is a randomly provisioned stable UUID associated with the device token. It is not the ESP32 MAC address.

```json
{
  "protocol_version": "1.0",
  "message_id": "1e87f9e5-0c11-4e94-b6f3-1712136f55ab",
  "message_type": "device.register",
  "timestamp": "2026-11-20T12:00:00Z",
  "payload": {
    "device_id": "c77e7516-0ca7-4dc5-965c-1b4bc49d84e6",
    "boot_id": "aa9df4a6-d853-40e3-93ce-826f820cb739",
    "hardware_model": "ESP32-WROOM-32",
    "firmware_version": "0.1.0",
    "uptime_ms": 1420,
    "capabilities": ["display", "touch", "mic_capture", "speaker_playback"]
  }
}
```

Validation: UUIDs for `device_id` and `boot_id`; bounded non-empty model and version strings; `uptime_ms` is a non-negative 32-bit integer; capabilities are unique strings from a server-maintained allowlist. Report only capabilities the installed firmware can actually use.

### 3.2 `device.registered`

Sent by the server only after successful handshake authentication and registration. The device must receive this before sending heartbeat/status or acting on commands.

```json
{
  "protocol_version": "1.0",
  "message_id": "2f005f16-4ccb-4a68-875e-b95c633a314f",
  "message_type": "device.registered",
  "timestamp": "2026-11-20T12:00:00Z",
  "payload": {
    "accepted": true,
    "device_id": "c77e7516-0ca7-4dc5-965c-1b4bc49d84e6",
    "session_id": "0448b2df-c0eb-4be2-8eb4-f8c80511775a",
    "server_time": "2026-11-20T12:00:00Z",
    "heartbeat_interval_s": 30,
    "heartbeat_timeout_s": 90,
    "max_message_bytes": 4096
  }
}
```

Validation: `accepted` must be `true`; session/device IDs are UUIDs; intervals are positive bounded integers. Rejection should normally happen before the WebSocket upgrade with HTTP 401/403. If a post-upgrade rejection is necessary, close with policy code 1008 and a short, non-sensitive close reason.

### 3.3 `device.heartbeat`

Sent every 30 seconds after registration, with ±3 seconds of device-side jitter. `sequence` starts at 1 on each `boot_id` and increases by one for every heartbeat.

```json
{
  "protocol_version": "1.0",
  "message_id": "3df4bab7-9984-46af-bcd4-b9746f5daa4f",
  "message_type": "device.heartbeat",
  "timestamp": "2026-11-20T12:00:30Z",
  "payload": {
    "boot_id": "aa9df4a6-d853-40e3-93ce-826f820cb739",
    "sequence": 1,
    "uptime_ms": 31420
  }
}
```

Validation: registered session required; boot ID must match registration; sequence is a positive 32-bit integer and must advance. A duplicate sequence may be acknowledged but must not refresh the server's last-valid-heartbeat time.

### 3.4 `device.heartbeat.ack`

Sent by the server after accepting a heartbeat. `reply_to` references the heartbeat envelope's `message_id`.

```json
{
  "protocol_version": "1.0",
  "message_id": "4f7dc3cb-745b-4b0f-8a3d-6cdfb85053b6",
  "message_type": "device.heartbeat.ack",
  "timestamp": "2026-11-20T12:00:30Z",
  "payload": {
    "reply_to": "3df4bab7-9984-46af-bcd4-b9746f5daa4f",
    "sequence": 1,
    "server_time": "2026-11-20T12:00:30Z"
  }
}
```

Validation: `reply_to` and sequence must match an accepted heartbeat. The device uses `server_time` to correct its clock estimate when needed.

### 3.5 `device.status`

Send after registration, on meaningful state changes, and at most once per second. Do not send high-rate telemetry. `state` is one of `idle`, `listening`, `processing`, `speaking`, or `error`.

```json
{
  "protocol_version": "1.0",
  "message_id": "5904f272-80a3-4e18-b274-4986c5ff328f",
  "message_type": "device.status",
  "timestamp": "2026-11-20T12:00:31Z",
  "payload": {
    "boot_id": "aa9df4a6-d853-40e3-93ce-826f820cb739",
    "sequence": 2,
    "state": "idle",
    "wifi_rssi_dbm": -54,
    "free_heap_bytes": 181240,
    "last_command": null
  }
}
```

Validation: status sequence increases independently from the heartbeat sequence and resets per boot; RSSI is within the ESP32-reported dBm range; heap is a non-negative integer. A duplicate status is ignored and a lower sequence is rejected. When reporting a command outcome, `last_command` is an object with `command_id` UUID and `state` of `accepted`, `completed`, `rejected`, or `failed`. It is null when there is no recent command result.

### 3.6 `server.ping`

Optional application-level probe from server to device. Routine transport liveness uses WebSocket control Ping/Pong frames, and device liveness uses `device.heartbeat`; this JSON probe is for diagnostics or a targeted application check.

```json
{
  "protocol_version": "1.0",
  "message_id": "64d6341d-fbc0-467b-b991-88f2671dd90f",
  "message_type": "server.ping",
  "timestamp": "2026-11-20T12:00:40Z",
  "payload": {
    "nonce": "ec82920d-3304-49f5-9b51-edaf17c1484f"
  }
}
```

Validation: nonce is an opaque UUID/string no longer than 64 characters. The device responds within 10 seconds or the diagnostic probe is marked missed; a single missed app-level probe does not itself disconnect a device.

### 3.7 `server.pong`

Sent by the device in response to `server.ping`.

```json
{
  "protocol_version": "1.0",
  "message_id": "7e3b4f77-73d6-4a50-aefc-8ed27568fba4",
  "message_type": "server.pong",
  "timestamp": "2026-11-20T12:00:40Z",
  "payload": {
    "reply_to": "64d6341d-fbc0-467b-b991-88f2671dd90f",
    "nonce": "ec82920d-3304-49f5-9b51-edaf17c1484f"
  }
}
```

Validation: `reply_to` must refer to a recent outstanding `server.ping`; nonce must match. Unsolicited pongs are ignored and counted as protocol errors.

### 3.8 `server.command`

Commands are allowlisted, small, expiring requests. V1 should begin with safe, idempotent display/UI commands only; never accept executable code, shell commands, arbitrary URLs, or arbitrary hardware pin operations.

```json
{
  "protocol_version": "1.0",
  "message_id": "8da91403-74b5-4d34-9ec6-a9d07631f134",
  "message_type": "server.command",
  "timestamp": "2026-11-20T12:00:45Z",
  "payload": {
    "command_id": "037b581b-8926-471f-9300-7417b126abaf",
    "name": "display.show_message",
    "arguments": {
      "text": "Welcome back",
      "duration_ms": 3000
    },
    "expires_at": "2026-11-20T12:01:15Z"
  }
}
```

Validation: UUID command ID, name from a versioned allowlist, arguments validated per command, and an expiry no more than five minutes ahead. A future device implementation can report the outcome in `device.status.last_command` or `device.error`. Backend V1 validates this schema but does not execute or dispatch commands. A future non-idempotent command needs a durable device-side deduplication/ack strategy before it is allowed.

### 3.9 `device.error`

Sent for a rejected or failed message/command when the connection remains usable. Do not include stack traces, secret values, authorization headers, or raw untrusted input.

```json
{
  "protocol_version": "1.0",
  "message_id": "9ea803c1-9b53-4da8-bccc-213f5f0b56e9",
  "message_type": "device.error",
  "timestamp": "2026-11-20T12:00:46Z",
  "payload": {
    "code": "unsupported_command",
    "message": "Command is not supported by this firmware.",
    "related_message_id": "8da91403-74b5-4d34-9ec6-a9d07631f134",
    "retryable": false
  }
}
```

Allowed V1 codes: `invalid_json`, `invalid_payload`, `unsupported_message_type`, `unsupported_version`, `unsupported_command`, `command_expired`, `busy`, and `internal_error`. `message` is bounded to 256 characters; `related_message_id` is optional; `retryable` is boolean.

## 4. Connection lifecycle

1. **Wi-Fi:** Connect using the existing Wi-Fi path. Wait until station mode reports connected. Keep the display/input loop responsive; do not block it while retrying network operations.
2. **WebSocket handshake:** Connect to `/ws/device`, offering subprotocol `deskbot.v1`. Include a per-device bearer token in the HTTP upgrade's `Authorization` header. Do not put credentials in the URL/query string. The server authenticates before accepting the upgrade and selects the subprotocol.
3. **Register:** Send exactly one `device.register` per connection. Wait up to 10 seconds for `device.registered`; no command is accepted before this point. Reject an unsupported protocol version or token/device mismatch.
4. **Operate:** Send status on state changes and heartbeats on schedule. Keep all callbacks short; defer JSON parsing or command work if it could delay Wi-Fi, touch, or audio tasks. Backend V1 does not dispatch commands or application-level pings.
5. **Heartbeat timeout:** Device sends `device.heartbeat` every 30 seconds with ±3 seconds jitter. Server marks the device stale after 90 seconds without a new valid heartbeat and closes the socket. A duplicate sequence can receive an ACK but does not refresh that deadline. Future firmware reconnects after two consecutive heartbeat acknowledgements are missed (about 60 seconds), unless a policy/authentication close requires configuration repair instead.
6. **Transport liveness:** Uvicorn `websockets-sansio` is explicitly selected with 20-second transport ping interval and timeout. These control frames are separate from Deskbot JSON messages.
7. **Disconnect:** Server catches `WebSocketDisconnect`, removes the session only if it still owns the device's current session, and marks the device offline. Device clears session state and reconnects. Treat Wi-Fi loss, TCP reset, timeout, and server restart as transient; treat HTTP 401/403 or close 1008 as a credential/protocol issue that needs bounded retry and diagnostics.
8. **Duplicate device connection:** Permit only one authenticated active session per `device_id`. A newly authenticated registration replaces the old session; close the old one with private-use code 4001 (`superseded`). Use a per-device lock/generation ID so cleanup from the old socket cannot mark the new session offline.
9. **Graceful server shutdown:** Close existing sockets with 1001 (`going away`), cancel send tasks cleanly, and clear in-memory sessions. Devices reconnect with jitter after the service returns.

The session registry is process-local for V1 and Uvicorn runs one worker. Multiple workers have separate memory and can disagree about the active device; add shared coordination before enabling more than one worker. This implementation intentionally does not support multi-worker deployments.

## 5. Reconnection strategy

For future firmware, use a single application-owned reconnect state machine. Do not combine the library's fixed reconnect interval with a second independent retry loop. This milestone does not implement firmware reconnection.

For attempt `n` starting at zero, use full jitter:

```text
delay = random(0, min(60 seconds, 1 second * 2^n))
```

Use a monotonic clock for delay calculation. Reset the attempt counter after the connection has remained registered for at least 60 seconds. Reconnect on transient network/server failures and abnormal closes. Do not tight-loop on DNS/TCP failures. For authentication, unsupported-version, or policy rejection, pause automatic reconnect (or use a long capped backoff) and surface a clear device status instead of hammering the server. Reconnect Wi-Fi first if station connectivity was lost.

On server startup, a device that has not yet reconnected is simply offline; no stale server-side connection is considered live after process restart. On device reboot, generate a new `boot_id`, reset heartbeat/status sequences, and keep the stable provisioned `device_id`.

## 6. Errors, limits, and slow clients

| Condition | Response |
| --- | --- |
| Invalid JSON or invalid UTF-8 | Close 1007 (invalid data). Do not echo malformed input. |
| Unknown message type or unsupported version | Close 1008 (policy/protocol violation) with a short, non-sensitive reason. |
| Well-formed envelope, malformed payload | Log a sanitized protocol error; allow at most three schema strikes per minute, then close 1008. `device.error` remains device-to-server only; the server has no JSON error message in V1. |
| Text message over 4,096 bytes | Close immediately with 1009 (message too big); do not parse or echo it. |
| Binary frame in V1 | Close 1003 (unsupported data); do not attempt to interpret it as JSON. |
| Authentication failure | Reject the HTTP upgrade with 401/403 before `accept`; do not reveal whether a device ID exists. |
| Device heartbeat timeout | Close with 1001 or a documented private close code; mark offline. |
| Server restart | Close with 1001 so the device retries. |
| Server overload / full outbound queue | Drop/coalesce replaceable status updates first; if commands cannot be queued safely, close 1013 (try again later). |
| Unexpected application failure | Log a correlation ID and sanitized error; close 1011 (internal error). |

Use bounded per-connection queues (initial proposal: at most 32 messages or 64 KiB outbound, whichever is reached first). Coalesce superseded `device.status` snapshots. Never silently discard commands; report a rejection or close so the server can retry before command expiry. Apply per-connection rate limits: one heartbeat per interval, status at most once per second, and a small burst allowance for registration/recovery.

The backend configures Uvicorn with a 4 KiB maximum, 20-second transport Ping/Pong interval and timeout, and disabled per-message compression. The selected `websockets-sansio` implementation does not support `ws_max_queue`; inbound frames are processed sequentially, and each device has a bounded outbound queue of 32 messages. [Uvicorn settings](https://www.uvicorn.dev/settings/)

## 7. Authentication and network security

- Use a random, unique, revocable token per device (at least 256 bits from a cryptographically secure generator). Map the token to the provisioned UUID `device_id`; do not authenticate by MAC address, IP address, or a shared fleet-wide password.
- Send the token as `Authorization: Bearer …` during the HTTP WebSocket upgrade, not in JSON, URL parameters, logs, or error messages. Store only a verifier/hash server-side and compare secrets in constant time.
- Require `wss://` with certificate and hostname validation on production networks. Do not use an insecure TLS mode. For a private LAN certificate, provision a private CA root to firmware or use a controlled certificate bundle; plan a certificate/token rotation process.
- Plain `ws://WINDOWS_LAN_IP:8000/ws/device` is acceptable only for local development or a physically/trust-isolated network without sensitive commands. A bearer token on `ws://` is observable and replayable. Discover `WINDOWS_LAN_IP` using `ipconfig`; it is not a backend constant.
- Restrict inbound port 8000 to trusted LAN devices. Do not expose the service or unauthenticated WebSocket route to the public internet. Validate `Origin` if browser clients are ever introduced; it is not a device-authentication mechanism.
- Rate-limit handshake attempts by source/device, cap concurrent unauthenticated handshakes, and log connection/session IDs without credentials or full sensitive payloads.
- The ESP32 MAC address may be recorded as optional diagnostics only; it is public metadata and never an authentication factor.

RFC 6455 defines the WebSocket handshake, subprotocol negotiation, control frames, and security considerations. FastAPI exposes WebSocket disconnects as `WebSocketDisconnect`, which should be handled as normal lifecycle cleanup rather than an unhandled application error. [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455), [FastAPI WebSocket handling](https://fastapi.tiangolo.com/advanced/websockets/)

## 8. Future binary audio compatibility

Audio streaming is **not part of V1**. V1 rejects binary frames. Reserve a future negotiated subprotocol such as `deskbot.v2` or a compatible protocol revision for audio rather than changing V1's JSON payload meaning.

Future audio design should use a small JSON control message to start/stop a stream and describe `stream_id`, codec, sample rate, channel count, sample format, and chunk duration. Binary frames then carry bounded audio chunks with a compact version/stream/sequence header. Define maximum chunk size, ordering, loss behavior, backpressure, and an explicit end-of-stream marker before implementation. Do not base64-encode audio into V1 JSON. Keep text/control and binary audio paths separate, and negotiate the feature through registration capabilities.

## 9. Selected lightweight ESP32 Arduino client

The firmware uses **Links2004/arduinoWebSockets** via its `WebSocketsClient` API from PlatformIO. Its project documentation explicitly supports ESP32 Arduino client mode, text and binary frames, close and Ping/Pong control frames, and TLS client use; the client API includes custom handshake headers and reconnection interval controls. Its receive buffer is bounded by `WEBSOCKETS_MAX_DATA_SIZE`, which fits this protocol's small-message design. The implementation builds against the project's current ESP32 Arduino core and uses `begin(host, port, path, "deskbot.v1")` plus `setAuthorization("Bearer ?")` for subprotocol and authentication headers. [Project README and supported features](https://github.com/Links2004/arduinoWebSockets), [WebSocketsClient API](https://github.com/Links2004/arduinoWebSockets/blob/master/src/WebSocketsClient.h), [library metadata](https://github.com/Links2004/arduinoWebSockets/blob/master/library.properties)

The library is LGPL-2.1; review its distribution/linking obligations before shipping firmware. Do not add the dependency or modify `platformio.ini` as part of this design task. Its high-level event callbacks should remain short; run `WebSocketsClient::loop()` frequently from the firmware's cooperative loop/task and keep reconnect timing under the protocol state machine's control.

## 10. Implemented backend and future firmware structure

Backend V1 is implemented in these files:

```text
server/app/
??? api/
?   ??? routes.py                  # Existing HTTP routes
?   ??? schemas.py                 # Existing HTTP response models
?   ??? websocket.py               # Handshake, registration, heartbeat/status loop
?   ??? websocket_schemas.py       # Strict envelope, typed payloads, bounded parser
??? security/
?   ??? device_auth.py             # Per-device token digest verification
??? services/
?   ??? device_sessions.py         # Single-process sessions, ownership and liveness
??? config.py                      # Bind, protocol, token, queue and timeout settings
??? main.py                        # FastAPI app, routes and graceful session shutdown
??? run.py                         # Explicit single-worker Uvicorn WebSocket configuration

server/scripts/ws_smoke_test.py    # Manual registration and heartbeat client
server/tests/
??? test_api.py                    # Existing HTTP API tests
??? test_device_auth.py            # Token generation and verification
??? test_device_sessions.py        # Ownership, bounded queues and liveness
??? test_websocket.py              # Handshake and session integration tests
??? test_websocket_protocol.py     # Envelope and payload validation
```

Session state is in process memory and the configured server uses one worker. Commands are validated but not sent or executed. There is no audio streaming.

### Implemented firmware structure

```text
firmware/deskbot-firmware/
??? include/
?   ??? secrets.h                    # Local-only credentials (ignored)
?   ??? secrets.example.h            # Safe configuration template
??? src/
    ??? main.cpp                     # Startup and responsive Arduino loop
    ??? network/
    ?   ??? wifi_manager.h/.cpp      # Existing Wi-Fi config and connectivity
    ?   ??? time_sync.h/.cpp         # Nonblocking SNTP synchronization
    ?   ??? device_websocket.h/.cpp  # Auth, lifecycle, messages, FreeRTOS reconnect worker
    ??? protocol/
        ??? deskbot_protocol.h/.cpp # Bounded JSON encoding/validation, UUID/time
```

The firmware tree is implemented without peripheral behavior. Keep one reconnect owner, avoid credential logging, and retain bounded JSON storage. The Links2004 client performs its TCP connect synchronously, so a failed attempt can briefly block the Arduino loop despite the nonblocking application timers.

## 11. Follow-up implementation plan

1. **Manual device configuration:** Set the Windows host, provisioned device UUID, and matching bearer token in the ignored firmware `secrets.h` and backend `.env`; preserve the existing Wi-Fi values.
2. **End-to-end LAN validation:** Connect the device to the Windows backend using the address discovered with `ipconfig`; test wrong credentials, replacement sessions, disconnects, server restart, stale heartbeat, and firewall behavior.
3. **Production transport security:** Before non-isolated use, terminate `wss://` with a trusted certificate, ensure firmware validates the certificate, and define token rotation/revocation procedures. Never disable TLS verification.
4. **Command delivery milestone:** Design bounded delivery, expiry, deduplication, acknowledgements, and persistence if required. Until that work is complete, do not execute commands.
5. **Audio milestone:** Separately specify and test negotiated binary audio framing, chunk bounds, backpressure, loss behavior, and end-of-stream handling. Do not add audio frames to V1.
