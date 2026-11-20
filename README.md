# Deskbot

Deskbot is a local-first smart desk assistant. An ESP32 handles the desk-side hardware and interface; a Debian home server runs the API and, as the project grows, speech and assistant services.

## Architecture

```text
ESP32 desk device                         Debian home server
┌─────────────────────────┐   Wi-Fi/API   ┌────────────────────────────┐
│ Display and touch       │ ────────────> │ FastAPI backend :8000      │
│ Microphone capture      │ <──────────── │ Speech / intent / actions  │
│ Speaker playback        │              │ Text-to-speech (planned)   │
└─────────────────────────┘              └────────────────────────────┘
```

The firmware owns hardware, display, touch input, microphone capture, speaker playback, and Wi-Fi. The backend owns the HTTP API and an authenticated V1 WebSocket connection for device registration, status and heartbeats. Speech recognition, intent processing, audio streaming, TTS, and command execution are not implemented. The status endpoint reports backend state only.

## Hardware

- ESP32-WROOM-32 DevKit V1 (30-pin)
- ILI9341 SPI touchscreen with resistive touch
- INMP441 I2S microphone
- MAX98357A I2S amplifier
- 8Ω 5W speaker

Network plan: Debian server `192.168.1.41` (future deployment), ESP32 `192.168.1.42` (DHCP reservation), backend port `8000`. During Windows development, discover the active machine IPv4 with `ipconfig`; do not use the future Debian address.

## Repository layout

```text
docs/                         Project documentation
firmware/deskbot-firmware/    ESP32 PlatformIO firmware
server/                       FastAPI backend and tests
```

See [Backend setup and deployment](server/README.md) for Windows development, Debian 13 systemd deployment, configuration, API routes, and tests.

See [WebSocket Protocol V1](docs/websocket-protocol-v1.md) for the complete protocol specification.

## Firmware

Firmware is built with PlatformIO using the Arduino framework and the `esp32dev` environment. Open `firmware/deskbot-firmware/platformio.ini` in PlatformIO. Build and upload from that directory with the PlatformIO CLI:

```shell
pio run
pio run --target upload
```

Generated `.pio` build output and local IDE state are excluded from Git; firmware source and project configuration are included.

### Local configuration

Copy `firmware/deskbot-firmware/include/secrets.example.h` to `firmware/deskbot-firmware/include/secrets.h` (ignored by Git) and fill in local values:

- `WIFI_SSID` / `WIFI_PASSWORD` — your 2.4 GHz Wi-Fi credentials
- `DESKBOT_WS_HOST` — current Windows development machine IPv4 (discover with `ipconfig`, e.g., `192.168.1.50`)
- `DESKBOT_WS_PORT` — backend port, normally `8000`
- `DESKBOT_WS_PATH` — `/ws/device`
- `DESKBOT_DEVICE_ID` — the provisioned device UUIDv4 (same as the key in backend token configuration)
- `DESKBOT_DEVICE_TOKEN` — its unique 64-character hexadecimal bearer token

Generate a development identity and token on Windows with Python. This prints the token once; keep it local:

```powershell
python -c "import hashlib,secrets,uuid; token=secrets.token_hex(32); device=uuid.uuid4(); print('DEVICE_ID='+str(device)); print('DEVICE_TOKEN='+token); print('TOKEN_SHA256='+hashlib.sha256(token.encode()).hexdigest())"
```

Put the UUID and token in the ignored firmware `secrets.h`. Put only the UUID-to-digest mapping in the backend's ignored `server/.env`:

```dotenv
DESKBOT_DEVICE_TOKEN_HASHES={"DEVICE_UUID":"TOKEN_SHA256"}
```

Replace both placeholders with the generated values. Restart the backend after changing its token verifier. The backend must bind to `0.0.0.0:8000`; allow inbound TCP 8000 in Windows Defender Firewall for the Private profile/local subnet. The device connects to `ws://WINDOWS_LAN_IP:8000/ws/device`. Plain `ws://` exposes the bearer token to LAN observers; use only on a trusted development LAN. Production requires WSS with certificate validation.

The firmware waits for SNTP UTC synchronization before opening the WebSocket. The device therefore needs working DNS and outbound NTP access (UDP 123) as well as LAN access to the backend.

### Build

From `firmware/deskbot-firmware/`:

```powershell
$pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"
& $pio run -t clean -e esp32dev
& $pio run -e esp32dev
```

If `platformio` is on `PATH`, the equivalent commands are `pio run -t clean` and `pio run`.

### Upload and Serial Monitor

Connect the ESP32 over USB. List serial devices, identify the board's USB-to-UART port, and close any serial monitor before uploading:

```powershell
$pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"
& $pio device list
& $pio run -e esp32dev -t upload --upload-port COM5
& $pio device monitor --port COM5 --baud 115200
```

Replace `COM5` with the port shown for the ESP32. Press the board's reset button if the monitor opens before startup output appears. Expected logs identify Wi-Fi/SNTP progress, WebSocket registration, and reconnect scheduling. Logs do not include the Wi-Fi password, device token, or Authorization header.

### Protocol behavior and limits

- The boot UUID is generated once after Wi-Fi starts and stays fixed across reconnects; heartbeat and status sequences are independent and start at 1 for that boot.
- Registration completes before status or heartbeat messages are sent. Heartbeats use the backend-advertised interval (normally 30 seconds) and must be acknowledged within 15 seconds.
- A valid server application ping receives `server.pong`; transport Ping/Pong is handled by the WebSocket library.
- Incoming application JSON is capped at 4,096 bytes and parsed with a fixed 1,536-byte ArduinoJson document and bounded nesting. Binary, fragmented, malformed, unknown, or out-of-order messages close the session.
- The WebSockets library has a separate fixed 15 KiB internal receive buffer. Firmware still rejects application messages over 4 KiB after delivery.
- The application reconnect state machine runs in its own FreeRTOS worker, so Links2004's synchronous TCP connect attempt does not block Arduino's main `loop()` task.

Current memory usage: **RAM 16.0% (52,472 bytes)**, **Flash 83.4% (1,093,289 bytes)**.

## Backend development

The backend requires Python 3.11 or newer. From the repository root on Windows PowerShell:

```powershell
cd server
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m app.run
```

If `.venv` does not exist yet, create it first with `py -3.11 -m venv .venv`, then activate it as above.

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell session, then activate again.

Copy `.env.example` to `.env` in `server/` for local configuration. Environment variables override values in `.env`. Keep device token verifiers and device bearer tokens out of Git. The backend stores only a SHA-256 token verifier mapped to one provisioned device UUID.

```powershell
python -m app.run
```

The configured defaults bind to `0.0.0.0:8000`; use <http://127.0.0.1:8000/docs> and <http://127.0.0.1:8000/api/v1/health> locally. The WebSocket path defaults to `/ws/device`. `app.run` explicitly selects Uvicorn's `websockets-sansio` backend, 4,096-byte message limit, 20-second transport ping interval/timeout, disabled per-message compression, and one worker. Development reload is intentionally omitted because one in-memory session registry requires one process.

### Provision a local development device

Generate a new device UUID, a 256-bit token, and the server-side digest. This prints the token once in your PowerShell window; do not paste it into source or documentation:

```powershell
python -c "import hashlib,secrets,uuid; token=secrets.token_hex(32); print('DEVICE_ID='+str(uuid.uuid4())); print('DEVICE_TOKEN='+token); print('TOKEN_SHA256='+hashlib.sha256(token.encode()).hexdigest())"
```

Add only the UUID-to-digest mapping to the ignored `server/.env` file (use the values from the command):

```dotenv
DESKBOT_DEVICE_TOKEN_HASHES={"DEVICE_UUID":"TOKEN_SHA256_DIGEST"}
```

For the simulator process, set the token and UUID in its environment (replace the placeholders locally):

```powershell
$env:DESKBOT_DEVICE_ID = "DEVICE_UUID"
$env:DESKBOT_DEVICE_TOKEN = "DEVICE_TOKEN"
```

Start or restart the backend after changing verifier configuration. To revoke or rotate a token, remove or replace that device's digest mapping and restart the process. Never put the bearer token in a URL, JSON message, or log.

### Connect the ESP32 over the LAN

Run `ipconfig` and use the active Windows adapter's IPv4 address. On the current development network the address discovered during this implementation was `192.168.1.50`; it can change, so discover it again when needed. The eventual device URL format is `ws://WINDOWS_LAN_IP:8000/ws/device`; no Windows IP is embedded in the backend. Plain `ws://` exposes the bearer token to LAN observers and is for an isolated, trusted development LAN only. Production should use `wss://` with normal certificate validation.

If the device cannot connect, allow inbound TCP port 8000 in Windows Defender Firewall for the Private profile and local subnet only. Do not turn the firewall off. From an elevated PowerShell prompt, for example:

```powershell
New-NetFirewallRule -DisplayName "Deskbot backend (Private LAN)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

### Test registration and a heartbeat without the ESP32

With `.venv` active and the development token environment set as above, start the service in one PowerShell window:

```powershell
python -m app.run
```

Then use another PowerShell window, activate the same environment, set `DESKBOT_DEVICE_ID` and `DESKBOT_DEVICE_TOKEN`, and run:

```powershell
python scripts\ws_smoke_test.py --url ws://127.0.0.1:8000/ws/device
```

The script offers `deskbot.v1`, sends `device.register`, prints `device.registered`, then sends heartbeat sequence 1 and prints the matching `device.heartbeat.ack`. It uses `websockets.sync.client` and does not require hardware.

### Run tests

```powershell
python -m pytest
```

All 49 tests pass, covering HTTP API routes, device authentication, session ownership, WebSocket handshake, protocol validation, and error handling.

The WebSocket endpoint is `/ws/device`; HTTP API paths remain unchanged. V1 keeps session state in process memory, so run exactly one Uvicorn worker. There is no server-originated `device.error`: server rejections use sanitized close codes/reasons. `server.command` is modeled only and no commands are executed.

## Current API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Welcome message and service metadata |
| `GET` | `/api/v1/health` | Backend health and UTC timestamp |
| `GET` | `/api/v1/status` | Backend service metadata and status |
| `GET` | `/docs` | Swagger UI |
| WebSocket | `/ws/device` | Authenticated device registration, heartbeat and status |

The existing HTTP endpoints are unauthenticated. The device WebSocket requires a per-device bearer token, but `ws://` sends that token without encryption and is suitable only for an isolated, trusted LAN. Use `wss://` with certificate validation before production exposure. Keep the backend off the public internet and restrict network access with the firewall.

## Documentation

- [WebSocket Protocol V1](docs/websocket-protocol-v1.md) — Complete protocol specification
- [Backend README](server/README.md) — Setup, deployment, configuration, and testing guide
- [Firmware README](firmware/deskbot-firmware/README.md) — Build, upload, configuration, and protocol behavior

## Implementation status

**Implemented:**
- ESP32 Wi-Fi connectivity with automatic reconnect
- SNTP time synchronization (UTC)
- Links2004 WebSockets client with `deskbot.v1` subprotocol
- Per-device bearer token authentication (SHA-256 verifier on server)
- Device registration with capabilities and boot identity
- Heartbeat with independent sequence counter and acknowledgment
- Status reporting with independent sequence counter
- Server application ping/pong
- Application-owned reconnect state machine with exponential backoff and jitter
- FastAPI backend with WebSocket endpoint `/ws/device`
- Single-process device session registry with ownership safety
- Bounded message queues, protocol strike limits, and inbound rate limits
- Comprehensive test suite (49 tests passing)

**Not implemented:**
- Display, touch, microphone, speaker peripherals
- Audio streaming (binary frames)
- Speech recognition, intent processing, TTS
- Command execution (`server.command` is modeled only)
- Multi-worker backend deployment (requires shared session coordination)
- Production TLS (`wss://`) termination and certificate validation
- Device provisioning workflow

## Planned future features

- Microphone capture and audio streaming over a future `deskbot.v2` binary protocol
- Display and touch interaction
- Speech recognition and intent processing on the backend
- Command execution with deduplication and acknowledgements
- Production TLS with certificate rotation
- Multi-worker backend with shared session store