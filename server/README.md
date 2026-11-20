# Deskbot Backend

The backend is a local-first FastAPI service intended to run on the Debian home server. It provides a versioned API foundation for future speech recognition, intent processing, command execution, and text-to-speech services. Hardware connectivity tracking is not implemented yet.

## Requirements

- Python 3.11 or newer
- Windows PowerShell for local development, or Debian 13 for deployment

## Windows development

From the repository root:

```powershell
cd server
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that PowerShell session, then activate again.

Copy `.env.example` to `.env` in `server/` for local configuration. Environment variables override values in `.env`.

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The interactive Swagger UI is at <http://127.0.0.1:8000/docs>. `--reload` is for development only.

Run the tests from `server/`:

```powershell
python -m pytest
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
ExecStart=/opt/deskbot/server/.venv/bin/uvicorn app.main:app --host 192.168.1.41 --port 8000
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
