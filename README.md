# Nexus — Network Fabric System

**Nexus** is the fleet management hub for the pr0t31n toolkit. It discovers, monitors, and manages tool instances across any network topology — VPN tunnels, mesh overlays, cloud VPCs, or direct LANs — from a single authenticated HTTPS dashboard. Nexus replaces manual SSH-and-check workflows with real-time fleet visibility, browser-based terminals, remote tool deployment, agent enrollment, job dispatch, and continuous security auditing.

```
https://localhost:6000
```

---

## Features

### Fleet Management

- **Three-tier discovery engine** — automatically finds tool instances across all configured networks:
  - **Tier 1: Active TCP scan** — probes known service ports across all fabric subnets with configurable parallelism (up to 256 workers) and timeout
  - **Tier 2: Agent checkin** — fleet nodes push their own status via `POST /api/fleet/checkin`, reporting installed tools, versions, OS, and architecture
  - **Tier 3: Adapter-driven discovery** — per-fabric-type adapters query native APIs (Tailscale, ZeroTier, AWS EC2, Proxmox, etc.) to discover nodes the port scan cannot reach
- **Node management** — view, edit, classify, and remove fleet nodes; assign device types (laptop, server, Raspberry Pi, VM, container, appliance, NUC, phone), OS family, architecture, VM roles, and display names
- **Multi-address nodes** — a single node can appear on multiple fabrics with different IPs; Nexus tracks each address-to-fabric mapping and shows all reachable paths
- **Offline detection** — configurable miss-count threshold marks tools and nodes as offline after consecutive scan failures
- **Real-time push** — SocketIO broadcasts fleet state changes to all connected browsers instantly; no polling or page reloads

### Network Fabrics

- **20 fabric types** across four categories, each with a guided configuration wizard:

| Category | Supported Types |
|----------|----------------|
| VPN | OpenVPN+Cloak, AmneziaWG, WireGuard, IPSec/IKEv2, OpenVPN, Cloudflare Tunnel |
| Overlay | Tailscale, Headscale, ZeroTier, Nebula, NetBird, Netmaker |
| Cloud | AWS VPC, Azure VNet, GCP VPC, Linode VPC, DigitalOcean VPC |
| Direct | Direct LAN, Proxmox |

- **Per-fabric health monitoring** — background probes check connectivity, certificate expiry, peer counts, and API reachability; health status shown in the sidebar
- **Fabric-aware routing** — discovery, terminal connections, and dispatch operations automatically route through the correct fabric
- **Encrypted credential storage** — fabric secrets (API keys, private keys, tokens) are encrypted at rest via Fernet symmetric encryption in the credential store

### SSH Terminal

- **Browser-based SSH** — connect to any fleet node directly from the Terminal page; sessions are bridged through paramiko to SocketIO with xterm.js rendering
- **Credential management** — store per-node SSH credentials (password or key-based) with encrypted secret storage; test connectivity before saving
- **Multi-session support** — open multiple terminal tabs to different nodes simultaneously; configurable session limits and idle timeouts
- **Session resize** — terminal dimensions follow the browser window; resize events propagate to the remote PTY in real time
- **SOCKS5 proxy** — create dynamic SOCKS5 tunnels through any fleet node for browser-based pivoting
- **Local port forwarding** — forward local ports to remote services through SSH tunnels; managed through the API with automatic cleanup

### Arsenal Bridge

- **Remote tool install/upgrade** — trigger Arsenal install scripts on any fleet node via SSH; supports single-node and batch operations across the fleet
- **Operation tracking** — every install/upgrade is logged as an arsenal operation with status, output capture, and timing
- **Version tracking** — periodically polls all fleet nodes for installed tool versions; flags version drift across the fleet
- **Install map** — knows the install script path for each pr0t31n tool; validates before dispatching

### Enrollment and Dispatch

- **Broker server** — a WebSocket-based command broker (port 5401) with mTLS mutual authentication; agents connect and maintain persistent command channels
- **Broker CA** — self-signed certificate authority for the broker; issues per-agent client certificates for mTLS enrollment
- **Agent enrollment** — issue and revoke agent certificates; track enrollment status per node
- **Job dispatch** — send commands to enrolled agents with progress tracking, output capture, timeout enforcement, and cancellation support
- **Appliance inventory** — view enrolled appliances with their installed tools, dispatch capabilities, and connection status

### Audit and Monitoring

- **Session monitoring** — background thread discovers and tracks all remote access sessions (SSH, RDP, VNC, mosh) to the Nexus server; enriches with username, auth method, key fingerprint, GeoIP, MAC address, and session type
- **Port monitoring** — tracks open listening ports/services on the Nexus server; detects opens/closes, enriches with process owner, binary hash, Arsenal tool matching, and firewall rule status; supports whitelisting persistent services and adding investigator notes
- **Node historian** — records state changes across the fleet: connect/disconnect events, IP changes, network configuration drift, software version history, certificate lifecycle, fabric membership changes, and OS/hardware drift
- **Audit log** — every significant action (login, config change, tool install, fabric edit, credential access, dispatch job, enrollment) is recorded with timestamp, username, source IP, event type, and severity
- **Retention reaper** — enforces configurable data retention policies: sessions (1 year), ports (3 years), audit log (90 days), node history (3 years)
- **Audit dashboard** — dedicated Audit page with filterable views of sessions, ports, audit log, and node history

### Custom Services (v2.6)

- **Third-party tool monitoring** — add any external service (vulnerability scanners, SIEM, ticketing systems, custom tools) to the fleet discovery loop
- **Three integration methods**:
  - **TCP Only** — port-open probe only; no API interaction
  - **API Polling** — authenticated HTTP requests to a status endpoint with configurable auth (API key, bearer token, basic auth, custom header), JSON path extraction, and TLS verification settings
  - **Webhook Receiver** — external tools push status updates to `POST /api/webhooks/<service_id>` with optional HMAC-SHA256 signature validation
- **Encrypted credentials** — API keys, tokens, and webhook secrets are encrypted at rest via the credential store
- **Management UI** — dedicated Services page with add/edit/delete forms, connectivity testing, and enable/disable toggle
- **Fleet integration** — custom services appear alongside built-in tools in the fleet dashboard; discovered on both local and fabric subnet scans

### UI and Theming

- **Sidebar navigation** — Fleet, Terminal, Arsenal, Enrollment, Dispatch, Audit, Services, Settings
- **Palette engine** — multiple color palettes with CSS custom property theming; dark and light modes persisted per browser
- **SocketIO live updates** — dashboard refreshes instantly on fleet changes; no polling
- **Session timeout warning** — countdown overlay with extend/logout options before session expiry
- **Responsive layout** — usable on tablets and narrow displays

---

## Monitored Services

### Built-in (hardcoded in TOOL_REGISTRY)

| Service     | Port | Protocol | Status API | Description |
|-------------|------|----------|------------|-------------|
| Crucible    | 5100 | HTTPS    | Yes        | Pentest orchestration platform |
| Gauntlet    | 5700 | HTTPS    | Yes        | Hardware/network device assessment |
| Quiver      | 5200 | HTTPS    | No         | Pentest hardware manager |
| Nexus       | 6000 | HTTPS    | Yes        | Fleet management hub (self) |
| Polaris     | 5900 | HTTPS    | Yes        | Tunnel manager |
| Beacon      | 9443 | HTTPS    | No         | Engagement status server |

Status API tools expose `GET /api/nexus-status` returning `{"scanning": true|false}`. This endpoint is intentionally unauthenticated so Nexus can poll without credentials.

### Custom Services (user-defined)

Any additional tool can be added through the Services page. Third-party tools like Nessus, Qualys, or custom internal tools are configured with their port, protocol, and integration method. Custom services participate in the same discovery loop as built-in tools.

---

## Architecture

```
                          Browser (HTTPS :6000)
                     SocketIO push ── fleet_update, term_output
                     Events ──────── request_scan, term_input, term_resize
                               │
                         Flask + SocketIO
                               │
         ┌─────────────────────┼─────────────────────────┐
         │                     │                          │
   ┌─────┴──────┐    ┌────────┴────────┐    ┌───────────┴──────────┐
   │ Blueprints │    │    Services     │    │    Background        │
   │            │    │                 │    │    Threads            │
   │ fleet      │    │ FleetDiscovery  │    │                      │
   │ fabrics    │    │ FabricManager   │    │ discovery loop (30s) │
   │ terminal   │    │ SSHTerminal     │    │ health probes        │
   │ arsenal    │    │ SSHTunnel       │    │ version tracker      │
   │ enrollment │    │ ArsenalBridge   │    │ session monitor      │
   │ dispatch   │    │ VersionTracker  │    │ port monitor         │
   │ audit      │    │ SessionMonitor  │    │ retention reaper     │
   │ admin      │    │ PortMonitor     │    │ node historian       │
   │ custom_svc │    │ RetentionReaper │    │ broker heartbeat     │
   └────────────┘    │ NodeHistorian   │    └──────────────────────┘
                     │ BrokerServer    │
                     │ BrokerCA        │
                     │ CredentialStore │
                     │ AuditMiddleware │
                     └────────┬────────┘
                              │
                     SQLite (WAL mode)
                     nexus.db — 16 tables
                     SCHEMA_VERSION = 7
                              │
         ┌────────────────────┼──────────────────────┐
         │                    │                       │
   fleet_nodes          network_fabrics         custom_services
   tool_instances       node_addresses          audit_log
   node_credentials     tunnels                 service_ports
   arsenal_operations   enrollment_jobs         remote_sessions
   dispatch_jobs        node_history            users / settings

   Broker (mTLS WebSocket :5401)
   ├── Agent enrollment + certificate issuance
   ├── Persistent command channels
   └── Dispatch job execution on enrolled nodes
```

---

## REST API

All endpoints require an authenticated session cookie unless noted otherwise.

### Fleet

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/fleet` | Full fleet payload (nodes, tools, fabrics, stats) |
| `POST` | `/api/fleet/scan` | Trigger an immediate discovery scan |
| `GET` | `/api/fleet/nodes` | List all fleet nodes with filtering |
| `GET` | `/api/fleet/nodes/<id>` | Get a single node with tools and addresses |
| `POST` | `/api/fleet/nodes/<id>` | Update node metadata |
| `DELETE` | `/api/fleet/nodes/<id>` | Remove a node from the fleet |
| `GET` | `/api/fleet/stats` | Aggregate fleet statistics |
| `POST` | `/api/fleet/checkin` | Agent checkin (public) |
| `GET` | `/api/nexus-status` | Public status endpoint — `{"scanning": bool}` |

### Fabrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/fabrics` | List all network fabrics |
| `POST` | `/api/fabrics` | Create a new fabric |
| `GET` | `/api/fabrics/<id>` | Get fabric details |
| `PUT` | `/api/fabrics/<id>` | Update fabric configuration |
| `DELETE` | `/api/fabrics/<id>` | Delete a fabric |
| `POST` | `/api/fabrics/test` | Test fabric connectivity |
| `POST` | `/api/fabrics/<id>/health` | Force health check |
| `POST` | `/api/fabrics/<id>/toggle` | Enable/disable a fabric |
| `GET` | `/api/fabric-types` | List supported fabric types and their wizard fields |

### Terminal and Tunnels

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/terminal/sessions` | List active SSH sessions |
| `POST` | `/api/terminal/connect` | Open a new SSH session |
| `POST` | `/api/terminal/disconnect` | Close an SSH session |
| `GET` | `/api/tunnels` | List active SSH tunnels |
| `POST` | `/api/tunnels/socks` | Create a SOCKS5 proxy tunnel |
| `POST` | `/api/tunnels/forward` | Create a local port forward |
| `DELETE` | `/api/tunnels/<id>` | Tear down a tunnel |

### Credentials

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/credentials/<node_id>` | List credentials for a node |
| `POST` | `/api/credentials` | Create/update SSH credentials |
| `DELETE` | `/api/credentials/<id>` | Delete a credential |
| `POST` | `/api/credentials/<id>/test` | Test SSH connectivity |

### Arsenal

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/arsenal/operations` | List recent operations |
| `GET` | `/api/arsenal/operations/<id>` | Get operation details |
| `POST` | `/api/arsenal/run` | Install/upgrade a tool on a node |
| `POST` | `/api/arsenal/batch` | Batch install across multiple nodes |
| `POST` | `/api/arsenal/cancel/<id>` | Cancel a running operation |
| `GET` | `/api/arsenal/tools` | List installable tools |
| `GET` | `/api/arsenal/versions` | Fleet-wide version matrix |
| `GET` | `/api/arsenal/versions/summary` | Version drift summary |
| `POST` | `/api/arsenal/versions/refresh` | Force version re-poll |

### Enrollment

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/enrollment/status` | Enrollment system status |
| `GET` | `/api/broker/status` | Broker server status |
| `POST` | `/api/enrollment/issue` | Issue an agent certificate |
| `POST` | `/api/enrollment/revoke` | Revoke an agent certificate |
| `POST` | `/api/fleet/enroll` | Enroll a node |
| `GET` | `/api/fleet/enroll/<id>` | Get enrollment job status |
| `GET` | `/api/fleet/enrollments` | List all enrollments |
| `GET` | `/api/agent/download` | Download agent package |
| `GET` | `/api/agent/binaries` | List available agent binaries |

### Dispatch

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/fleet/dispatch` | Dispatch a command to an agent |
| `GET` | `/api/fleet/dispatch/<id>` | Get dispatch job status |
| `POST` | `/api/fleet/dispatch/<id>/stop` | Stop a running dispatch job |
| `GET` | `/api/fleet/dispatch/jobs` | List all dispatch jobs |
| `GET` | `/api/fleet/appliances` | List enrolled appliances |
| `GET` | `/api/fleet/appliances/<id>/tools` | Tools on an appliance |

### Audit

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/audit/sessions` | List remote access sessions |
| `GET` | `/api/audit/sessions/<id>` | Session details |
| `GET` | `/api/audit/ports` | List monitored ports |
| `GET` | `/api/audit/ports/<id>` | Port details |
| `POST` | `/api/audit/ports/<id>/whitelist` | Whitelist a port |
| `POST` | `/api/audit/ports/<id>/unwhitelist` | Remove from whitelist |
| `POST` | `/api/audit/ports/<id>/notes` | Add investigator notes |
| `GET` | `/api/audit/log` | Query audit log |
| `GET` | `/api/audit/node-history` | Fleet-wide node history |
| `GET` | `/api/audit/node-history/<id>` | Node-specific history |
| `GET` | `/api/audit/stats` | Audit statistics |
| `GET` | `/api/audit` | Admin audit log (settings page) |

### Custom Services

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/custom-services` | List all custom services |
| `POST` | `/api/custom-services` | Create a custom service (admin) |
| `PUT` | `/api/custom-services/<id>` | Update a custom service (admin) |
| `DELETE` | `/api/custom-services/<id>` | Delete a custom service (admin) |
| `POST` | `/api/custom-services/<id>/toggle` | Enable/disable (admin) |
| `POST` | `/api/custom-services/<id>/test` | Test TCP + API connectivity |
| `POST` | `/api/webhooks/<service_id>` | Webhook receiver (public, HMAC validated) |

### Settings and Auth

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings` | Get all settings |
| `POST` | `/api/settings` | Update settings |
| `POST` | `/api/install-tool` | Trigger local tool install |
| `POST` | `/api/session_ping` | Keep session alive |
| `POST` | `/login` | Authenticate |
| `GET` | `/logout` | End session |
| `POST` | `/auth/change-password` | Change password |

---

## Security

| Control | Detail |
|---------|--------|
| Transport | TLS 1.2+ minimum; self-signed RSA-4096 certificate generated on first run via `openssl` |
| Authentication | Session cookie — `Secure`, `HttpOnly`, `SameSite=Lax`; 30-minute lifetime with session ping keepalive |
| Passwords | Stored as `pbkdf2:sha256` hashes via Werkzeug; mandatory first-login password change; weak password/username rejection |
| CSRF | Per-session token in all state-mutating forms; validated with `secrets.compare_digest` |
| RBAC | Three roles: `admin`, `operator`, `viewer` — admin required for fleet changes, credential management, and service configuration |
| Credential Store | Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256) for all stored secrets: SSH passwords, SSH keys, fabric API keys, custom service credentials, webhook secrets |
| Broker mTLS | Self-signed CA issues per-agent client certificates for mutual TLS on the broker WebSocket (port 5401) |
| Headers | `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` |
| SocketIO | Unauthenticated `connect` events are rejected |
| Webhook auth | HMAC-SHA256 signature validation on inbound webhooks with timing-safe comparison |
| Public endpoints | Only `/api/nexus-status`, `/api/fleet/checkin`, and `/api/webhooks/*` are accessible without authentication |
| Audit trail | Every action logged with username, source IP, timestamp, event type, and severity |

---

## Requirements

- Python 3.10+
- System: `openssl` (TLS certificate generation)
- pip packages: `flask`, `flask-socketio`, `eventlet`, `requests`, `cryptography`, `paramiko`, `sshtunnel`, `fastapi`, `uvicorn`, `websockets`

---

## Installation

### Quick install (Linux / macOS)

```bash
cd /opt/pr0t31n/Nexus
sudo python3 install.py
```

This installs all Python dependencies and registers a boot-time service:

- **Linux** — systemd unit at `/etc/systemd/system/pr0t31n-nexus.service`
- **macOS** — LaunchDaemon at `/Library/LaunchDaemons/com.pr0t31n.nexus.plist`

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
pip install -r requirements.txt
python3 app.py
```

---

## First Login

Navigate to `https://localhost:6000`. Accept the self-signed certificate warning.

| Field    | Default value     |
|----------|-------------------|
| Username | `admin`           |
| Password | `t3mpPas$w0rd`    |

You will be redirected to a mandatory credential-change form before accessing the dashboard. The new username must not be a common default and the password must be at least 8 characters.

Credentials are stored as hashed values in `.auth.json` inside the Nexus directory. The session secret key lives in `.secret_key`.

---

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | Flask application, page routes, SocketIO handlers, service initialization |
| `config.py` | TOOL_REGISTRY, fabric types, RBAC roles, retention/monitoring defaults |
| `_auth.py` | Shared auth module — credentials, TLS cert, session config, CSRF, security headers |
| `install.py` | Cross-platform installer — pip, system packages, boot service |
| `models/db.py` | SQLite schema (16 tables), migrations, CRUD helpers |
| `services/fleet_discovery.py` | Three-tier fleet discovery engine + custom service probing |
| `services/fabric_manager.py` | Fabric lifecycle, health monitoring, adapter dispatch |
| `services/ssh_terminal.py` | Paramiko SSH sessions bridged to SocketIO |
| `services/ssh_tunnel.py` | SOCKS5 proxy and local port forwarding via SSH |
| `services/arsenal_bridge.py` | Remote tool install/upgrade via SSH |
| `services/version_tracker.py` | Fleet-wide tool version monitoring |
| `services/broker.py` | WebSocket command broker for enrolled agents |
| `services/broker_ca.py` | Self-signed mTLS CA for broker enrollment |
| `services/credential_store.py` | Fernet-encrypted credential storage |
| `services/session_monitor.py` | Remote access session discovery and tracking |
| `services/port_monitor.py` | Open port/service monitoring with whitelisting |
| `services/retention_reaper.py` | Data retention policy enforcement |
| `services/node_historian.py` | Fleet node state change history |
| `services/audit_middleware.py` | Request-level audit logging |
| `routes/fleet.py` | Fleet CRUD + discovery trigger + checkin endpoints |
| `routes/fabrics.py` | Fabric CRUD + health + type definitions |
| `routes/terminal.py` | SSH session + tunnel + credential endpoints |
| `routes/arsenal.py` | Arsenal operations + version tracking |
| `routes/enrollment.py` | Agent enrollment + broker status + certificate management |
| `routes/dispatch.py` | Job dispatch + appliance inventory |
| `routes/audit.py` | Session/port/log/history audit endpoints |
| `routes/custom_services.py` | Custom service CRUD + webhook receiver |
| `routes/admin.py` | Settings + admin audit log |
| `templates/layout.html` | Sidebar navigation, palette engine, session timeout |
| `templates/fleet.html` | Fleet dashboard with node cards |
| `templates/terminal.html` | SSH terminal with xterm.js |
| `templates/arsenal.html` | Arsenal operations and version matrix |
| `templates/enrollment.html` | Agent enrollment management |
| `templates/dispatch.html` | Job dispatch interface |
| `templates/audit.html` | Audit dashboard (sessions, ports, log, history) |
| `templates/custom_services.html` | Custom service management UI |
| `templates/settings.html` | Discovery, SSH, fabric, and audit settings |
| `static/css/style.css` | CSS custom property theming with palette engine |
| `static/js/nexus.js` | SocketIO client, fleet renderer, palette switcher |

---

## Database Schema

Nexus uses SQLite in WAL mode with 16 tables (schema version 7):

| Table | Purpose |
|-------|---------|
| `fleet_nodes` | Physical/virtual machines in the fleet |
| `network_fabrics` | VPN, overlay, cloud, and direct LAN configurations |
| `node_network_addresses` | Node-to-fabric IP address mappings |
| `tool_instances` | Discovered tool instances per node |
| `node_credentials` | Encrypted SSH credentials per node |
| `tunnels` | Active SSH tunnels (SOCKS5 and port forwards) |
| `arsenal_operations` | Tool install/upgrade operation history |
| `audit_log` | Timestamped audit trail of all actions |
| `users` | User accounts with roles |
| `settings` | Key-value application settings |
| `enrollment_jobs` | Agent enrollment status tracking |
| `dispatch_jobs` | Command dispatch jobs with output capture |
| `remote_sessions` | Discovered remote access sessions (SSH/RDP/VNC/mosh) |
| `service_ports` | Open listening ports with process enrichment |
| `node_history` | Fleet node state change timeline |
| `custom_services` | User-defined third-party service configurations |

---

## Uninstall

```bash
sudo python3 install.py --uninstall
```

This stops the service, disables it from booting, and removes the service file. Python packages and application files are left in place.

---

## Version

`2.6.0` — Current release
