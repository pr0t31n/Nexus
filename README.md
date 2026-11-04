# Nexus — Central Hub

**Nexus** is a lightweight, self-hosted dashboard that discovers and monitors every security platform instance running on your local network. It continuously probes all network interfaces for known tools, displays live scan status, and lets you jump directly to any discovered instance — all from a single authenticated HTTPS page.

```
https://localhost:6000
```

---

## Features

- **Automatic discovery** — scans every local IPv4 address × 7 service ports every 30 seconds
- **Live scan status** — polls `/api/nexus-status` on pr0t31n apps and shows `SCANNING` / `IDLE` badges in real time
- **Hostname deduplication** — instances that share a hostname (e.g., same device on Wi-Fi and Ethernet) are merged into a single card showing all IPs
- **SocketIO push updates** — the dashboard refreshes instantly when a scan completes; no page reload required
- **Manual scan trigger** — the "Scan Now" button fires an immediate sweep from any browser
- **One-click access** — each card links directly to the discovered instance; multi-IP cards list alternate URLs
- **Dark / light theme** — persisted per browser via `localStorage`
- **HTTPS** — self-signed RSA-4096 certificate generated on first run; TLS 1.2+ with strong ECDHE cipher suites
- **Authenticated** — session-based login with mandatory first-login password change and CSRF protection
- **Boot service** — installer registers a systemd unit (Linux) or LaunchDaemon (macOS) so Nexus starts automatically

---

## Monitored Services

| Service     | Port | Protocol | Scan Status |
|-------------|------|----------|-------------|
| Crucible    | 5100 | HTTPS    | ✓ (unified orchestration platform) |
| Nexus       | 6000 | HTTPS    | — (fleet hub) |
| Gauntlet    | 5700 | HTTPS    | ✓ (network device assessment) |
| Quiver      | 5200 | HTTPS    | — (hardware manager) |
| Polaris     | 5900 | HTTPS    | ✓ (tunnel manager) |
| Beacon      | 9443 | HTTPS    | — (engagement status) |
| Nessus      | 8834 | HTTPS    | — |

Scan status is retrieved by querying `GET /api/nexus-status` on apps that support it. The endpoint returns `{"scanning": true|false}` and is intentionally unauthenticated so Nexus can poll without credentials.

---

## Requirements

- Python 3.10+
- `openssl` (system binary — used to generate the self-signed TLS certificate on first run)
- pip packages: `flask`, `flask-socketio`, `eventlet`, `requests`, `werkzeug`

---

## Installation

### Quick install (Linux / macOS)

```bash
cd /path/to/Nexus
sudo python3 install.py
```

This installs all Python dependencies and registers a boot-time service:

- **Linux** — systemd unit at `/etc/systemd/system/pr0t31n-nexus.service`
- **macOS** — LaunchDaemon at `/Library/LaunchDaemons/com.pr0t31n.nexus.plist`
  - Logs: `/var/log/pr0t31n/nexus.log` / `nexus.err`

### Installer options

```
--check           Dry run — show what would be installed, make no changes
--skip-python     Skip pip install stage
--skip-system     Skip system package install stage (apt/brew)
--skip-service    Skip service registration
--uninstall       Stop and remove the boot service
```

### Manual run (no service)

```bash
pip install flask flask-socketio eventlet requests werkzeug
python3 app.py
```

---

## First login

Navigate to `https://localhost:6000`. Your browser will warn about the self-signed certificate — accept it to proceed.

| Field    | Default value     |
|----------|-------------------|
| Username | `admin`           |
| Password | `t3mpPas$w0rd`    |

You will be redirected to a mandatory password-change form before accessing the dashboard. The new password must be at least 8 characters.

Credentials are stored as bcrypt-compatible hashes in `.auth.json` inside the Nexus directory. The session secret key lives in `.secret_key`.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Browser (HTTPS :6000)              │
│  SocketIO ←── instances_update push event           │
│  "Scan Now" ──► request_scan event / POST /api/scan │
└─────────────────────────────────────────────────────┘
                          │
                    Flask + SocketIO
                          │
          ┌───────────────┴───────────────┐
          │     Background scanner        │
          │  (daemon thread, 30 s loop)   │
          └───────────────┬───────────────┘
                          │
         _local_ips()  ×  SERVICES (7 ports)
                          │
              _tcp_open() probe (0.4 s timeout)
                          │ (hit)
              _hostname_for() — gethostbyaddr
                          │
         (hostname, service_id) dedup key
                          │
         _scan_status() → GET /api/nexus-status
         (pr0t31n apps only, verify=False, 1.5 s)
                          │
               _instances{} updated
                          │
         socketio.emit("instances_update", ...)
```

### Key files

| File | Purpose |
|------|---------|
| `app.py` | Flask application, routes, SocketIO handlers, scanner |
| `_auth.py` | Shared auth module — credentials, TLS cert, session config, CSRF, security headers |
| `install.py` | Cross-platform installer — pip, system packages, boot service |
| `templates/base.html` | Nav bar, theme toggle, SocketIO bootstrap |
| `templates/index.html` | Instance grid with Jinja2 card macros |
| `static/css/style.css` | Full CSS variable theming (dark default, light toggle) |
| `static/js/main.js` | SocketIO listener, card renderer, scan trigger |
| `requirements.txt` | Pinned Python dependencies |

---

## REST API

All endpoints require an authenticated session cookie except `/api/nexus-status`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/instances` | Returns current discovered instances as JSON |
| `POST` | `/api/scan` | Starts an immediate background scan |
| `GET` | `/api/nexus-status` | Public — returns `{"scanning": false}` (Nexus itself has no scan) |

### `GET /api/instances` response

```json
{
  "last_scan": "2025-04-06T14:32:11.445Z",
  "instances": [
    {
      "id": "crucible",
      "name": "Crucible",
      "desc": "Pentest Orchestration",
      "icon": "crucible",
      "proto": "https",
      "port": 5100,
      "hostname": "pentest-box",
      "ips": ["192.168.1.42", "10.0.0.42"],
      "scanning": true,
      "url": "https://192.168.1.42:5100",
      "extra_urls": ["https://10.0.0.42:5100"]
    }
  ]
}
```

---

## Security

| Control | Detail |
|---------|--------|
| Transport | TLS 1.2+ minimum; ECDHE-AES256-GCM / CHACHA20-POLY1305 cipher suite; RC4/DES/NULL excluded |
| Certificate | Self-signed RSA-4096, 10-year validity, generated via `openssl req -x509` on first run |
| Authentication | Session cookie — `Secure`, `HttpOnly`, `SameSite=Lax`; 8-hour lifetime |
| Passwords | Stored as `pbkdf2:sha256` hashes via Werkzeug |
| CSRF | Per-session token in all state-mutating forms; validated with `secrets.compare_digest` |
| Headers | `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` |
| SocketIO | Unauthenticated `connect` events are rejected with `return False` |

The `/api/nexus-status` endpoint is intentionally public (no auth required) so that a parent Nexus instance can poll child instances without needing credentials.

---

## Theming

Nexus ships with a dark theme by default and a full light theme toggled via the sun/moon button in the nav bar. The preference is persisted to `localStorage` under the key `nexus-mode`. All colours are driven by CSS custom properties on `[data-mode="dark"]` / `[data-mode="light"]` selectors — no JavaScript involvement after the initial toggle.

---

## Uninstall

```bash
sudo python3 install.py --uninstall
```

This stops the service, disables it from booting, and removes the service file. Python packages and application files are left in place.

---

## Version

`2.5.5` — Current release
