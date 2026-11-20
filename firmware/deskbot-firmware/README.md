# Deskbot ESP32 Firmware

PlatformIO firmware for the ESP32-WROOM-32 DevKit V1. This milestone implements Wi-Fi, SNTP time synchronization, authenticated `deskbot.v1` WebSocket registration, heartbeat acknowledgements, status reports, application `server.ping` responses, and one reconnect state machine. Audio, display, touch, buttons, and command execution are not implemented.

## Dependencies

PlatformIO Core, the `espressif32` PlatformIO platform, Links2004 `WebSockets` 2.7.x, and ArduinoJson 6.21.x are declared in `platformio.ini`.

Links2004 `WebSocketsClient.begin()` accepts a path and subprotocol, and `setAuthorization()` supplies the handshake `Authorization` header. The application controls retry timing: it calls the library's event loop continuously only for an active connection and gates disconnected calls with its own exponential backoff and jitter.

## Local configuration

`include/secrets.h` is ignored by Git. It already contains the local Wi-Fi settings and must not be replaced with `secrets.example.h`. Keep the existing Wi-Fi values and fill in the WebSocket values in `secrets.h`:

- `DESKBOT_WS_HOST`: current Windows development machine IPv4 address, discovered with `ipconfig` (currently `192.168.1.50`).
- `DESKBOT_WS_PORT`: backend port, normally `8000`.
- `DESKBOT_WS_PATH`: `/ws/device`.
- `DESKBOT_DEVICE_ID`: the same provisioned UUIDv4 used as the key in backend token configuration.
- `DESKBOT_DEVICE_TOKEN`: its unique 64-character hexadecimal bearer token.

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

## Build

In PowerShell, from this directory:

```powershell
$pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"
& $pio run -t clean -e esp32dev
& $pio run -e esp32dev
```

If `platformio` is on `PATH`, the equivalent commands are `pio run -t clean` and `pio run`.

## Upload and Serial Monitor

Connect the ESP32 over USB. List serial devices, identify the board's USB-to-UART port, and close any serial monitor before uploading:

```powershell
$pio = Join-Path $env:USERPROFILE ".platformio\penv\Scripts\platformio.exe"
& $pio device list
& $pio run -e esp32dev -t upload --upload-port COM5
& $pio device monitor --port COM5 --baud 115200
```

Replace `COM5` with the port shown for the ESP32. Press the board's reset button if the monitor opens before startup output appears. Expected logs identify Wi-Fi/SNTP progress, WebSocket registration, and reconnect scheduling. Logs do not include the Wi-Fi password, device token, or Authorization header.

## Protocol behavior and limits

- The boot UUID is generated once after Wi-Fi starts and stays fixed across reconnects; heartbeat and status sequences are independent and start at 1 for that boot.
- Registration completes before status or heartbeat messages are sent. Heartbeats use the backend-advertised interval (normally 30 seconds) and must be acknowledged within 15 seconds.
- A valid server application ping receives `server.pong`; transport Ping/Pong is handled by the WebSocket library.
- Incoming application JSON is capped at 4,096 bytes and parsed with a fixed 1,536-byte ArduinoJson document and bounded nesting. Binary, fragmented, malformed, unknown, or out-of-order messages close the session.
- The WebSockets library has a separate fixed 15 KiB internal receive buffer. Firmware still rejects application messages over 4 KiB after delivery.
- The application reconnect state machine runs in its own FreeRTOS worker, so Links2004's synchronous TCP connect attempt does not block Arduino's main `loop()` task.

A successful build verifies compilation only. No physical connection or hardware behavior has been tested in this milestone.
