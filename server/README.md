# Deskbot Backend

The backend is a local-first FastAPI service for Deskbot. It preserves the HTTP API and provides an authenticated WebSocket V1 connection for one ESP32 device, including registration, status validation, and heartbeat acknowledgements. Speech recognition, audio streaming, TTS, and command execution are not implemented.

## Requirements

- Python 3.11 or newer
- Windows PowerShell for local development, or Debian 13 for deployment

## Windows development

From the repository root:

```powershell
cd server
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
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

Run the tests from `server/`:

```powershell
python -m pytest
```

The WebSocket endpoint is `/ws/device`; HTTP API paths remain unchanged. V1 keeps session state in process memory, so run exactly one Uvicorn worker. There is no server-originated `device.error`: server rejections use sanitized close codes/reasons. `server.command` is modeled only and no commands are executed.

### Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `DESKBOT_HOST` | `0.0.0.0` | Uvicorn bind host |
| `DESKBOT_PORT` | `8000` | Uvicorn bind port |
| `DESKBOT_WEBSOCKET_PATH` | `/ws/device` | Device WebSocket route |
| `DESKBOT_SERVICE_NAME`, `DESKBOT_VERSION`, `DESKBOT_ENVIRONMENT` | Project defaults | HTTP metadata |
| `DESKBOT_DEVICE_TOKEN_HASHES` | `{}` | JSON mapping of one device UUID to its SHA-256 token digest |
| `DESKBOT_WEBSOCKET_MAX_MESSAGE_BYTES` | `4096` | Complete text frame limit |
| `DESKBOT_WEBSOCKET_OUTBOUND_QUEUE_SIZE` | `32` | Per-session outbound queue limit |
| `DESKBOT_DEVICE_HEARTBEAT_INTERVAL_S` | `30` | Advertised heartbeat interval |
| `DESKBOT_DEVICE_HEARTBEAT_TIMEOUT_S` | `90` | Stale-session timeout |

Other bounded queue, registration, send-timeout, and rate-limit settings are also configurable with the corresponding `DESKBOT_` names defined in `server/app/config.py`.

Check the HTTP health route from a second PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health | ConvertTo-Json
```

## Debian 13 deployment (systemd, no Docker)

The following example installs the application at `/opt/deskbot`. Run deployment commands from an administrator account. The service itself runs as the dedicated, non-root `deskbot` account.

### Install system packages and create the service account

```bash
sudo apt update
sudo apt install --yes python3 python3-venv
sudo useradd --system --create-home --home-dir /var/lib/deskbot \
  --shell /usr/sbin/nologin deskbot
sudo install -d --owner=deskbot --group=deskbot --mode=0750 /opt/deskbot
```

Place or clone the repository into `/opt/deskbot`, then ensure the service account can read the source. For example, if the checkout is at `/opt/deskbot`:

```bash
sudo chown -R deskbot:deskbot /opt/deskbot
```

### Create the virtual environment and install runtime dependencies

```bash
sudo -u deskbot python3 -m venv /opt/deskbot/server/.venv
sudo -u deskbot /opt/deskbot/server/.venv/bin/python -m pip install --upgrade pip
sudo -u deskbot /opt/deskbot/server/.venv/bin/python -m pip install \
  -r /opt/deskbot/server/requirements.txt
```

The Debian system Python is left untouched; dependencies are installed only in the project virtual environment.

### Configure the service environment

The example below uses a service-specific file outside the repository so deployment configuration cannot be committed accidentally.

```bash
sudo install -d --owner=root --group=deskbot --mode=0750 /etc/deskbot
sudo install --owner=root --group=deskbot --mode=0640 \
  /opt/deskbot/server/.env.example /etc/deskbot/deskbot.env
sudoedit /etc/deskbot/deskbot.env
```

Set or confirm these values in `/etc/deskbot/deskbot.env`:

```dotenv
DESKBOT_SERVICE_NAME=Deskbot API
DESKBOT_VERSION=1.0.0
DESKBOT_ENVIRONMENT=production
DESKBOT_HOST=192.168.1.41
DESKBOT_PORT=8000
DESKBOT_WEBSOCKET_PATH=/ws/device
```

The API binds to `192.168.1.41:8000` below so it is reachable by the reserved ESP32 address on the LAN. Ensure the address is configured on the Debian host and restrict port 8000 with the host firewall to trusted LAN devices. The V1 endpoints have no authentication or TLS and should not be exposed to the public internet.

### systemd unit

Create `/etc/systemd/system/deskbot-api.service`:

```ini
[Unit]
Description=Deskbot FastAPI backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=deskbot
Group=deskbot
WorkingDirectory=/opt/deskbot/server
EnvironmentFile=/etc/deskbot/deskbot.env
ExecStart=/opt/deskbot/server/.venv/bin/python -m app.run
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

This production command intentionally has no development reload option.

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now deskbot-api.service
sudo systemctl status deskbot-api.service
```

`Restart=on-failure` asks systemd to restart the process after an unexpected failure. Uvicorn startup, shutdown, and error logs are sent to the system journal.

### Logs and troubleshooting

```bash
sudo journalctl -u deskbot-api.service -f
sudo journalctl -u deskbot-api.service --since today
sudo systemctl restart deskbot-api.service
sudo ss -ltnp | grep ':8000'
curl http://192.168.1.41:8000/api/v1/health
```

If the unit fails, check that the service account can read the checkout and execute the virtual environment, that `/etc/deskbot/deskbot.env` is readable by group `deskbot`, and that `192.168.1.41` is assigned to the host. Uvicorn cannot bind to an address that is not configured on the machine.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `DESKBOT_SERVICE_NAME` | `Deskbot API` | Service display name and API title |
| `DESKBOT_VERSION` | `1.0.0` | Service/API version metadata |
| `DESKBOT_ENVIRONMENT` | `development` | Environment label returned by status |

Application settings load `server/.env` (when present), then environment variables take precedence. The `.env` file is ignored by Git; `.env.example` is safe to track. Do not put secrets in the example file.

## API reference

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Welcome message and basic service metadata |
| `GET` | `/api/v1/health` | API process health and UTC timestamp |
| `GET` | `/api/v1/status` | Service name, version, environment, and backend status |
| `GET` | `/docs` | Swagger UI |

The status response describes the backend only; it does not claim that the ESP32 is connected.
