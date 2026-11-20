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

The firmware owns hardware, display, touch input, microphone capture, speaker playback, and Wi-Fi. The server owns the API and planned speech recognition, intent processing, command execution, and TTS. Hardware connection tracking is not implemented yet; the backend status endpoint reports backend state only. No WebSocket functionality is implemented yet.

## Hardware

- ESP32-WROOM-32 DevKit V1 (30-pin)
- ILI9341 SPI touchscreen with resistive touch
- INMP441 I2S microphone
- MAX98357A I2S amplifier
- 8Ω 5W speaker

Network plan: Debian server `192.168.1.41`, ESP32 `192.168.1.42` (DHCP reservation), backend port `8000`.

## Repository layout

```text
docs/                         Project documentation
firmware/deskbot-firmware/    ESP32 PlatformIO firmware
server/                       FastAPI backend and tests
```

See [Backend setup and deployment](server/README.md) for Windows development, Debian 13 systemd deployment, configuration, API routes, and tests.

## Firmware

Firmware is built with PlatformIO using the Arduino framework and the `esp32dev` environment. Open `firmware/deskbot-firmware/platformio.ini` in PlatformIO. Build and upload from that directory with the PlatformIO CLI:

```shell
pio run
pio run --target upload
```

Generated `.pio` build output and local IDE state are excluded from Git; firmware source and project configuration are included.

## Backend development

The backend requires Python 3.11 or newer. From the repository root on Windows PowerShell:

```powershell
cd server
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run backend tests from `server/`:

```powershell
python -m pytest
```

The development API is available at <http://127.0.0.1:8000>; Swagger UI is at <http://127.0.0.1:8000/docs>. Do not use development reload mode for production. Deployment instructions use systemd and a Python virtual environment; Docker is not used.

## Current API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Welcome message and service metadata |
| `GET` | `/api/v1/health` | Backend health and UTC timestamp |
| `GET` | `/api/v1/status` | Backend service metadata and status |
| `GET` | `/docs` | Swagger UI |

The V1 endpoints are intended for a trusted local network and do not provide authentication or TLS. Keep the backend off the public internet and restrict network access with the server firewall.
