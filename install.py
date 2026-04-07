#!/usr/bin/env python3
"""
install.py — Nexus installer
Installs all dependencies for the Nexus central hub and registers it as a
boot-time service (systemd on Linux, launchd on macOS).

Usage:
    sudo python3 install.py            # full install + service
    python3 install.py --check         # dry run — show what would change
    python3 install.py --skip-python   # skip pip install stage
    python3 install.py --skip-service  # skip service registration
    python3 install.py --uninstall     # remove service and unload it
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Colour helpers (ANSI — gracefully degraded on Windows)
# ─────────────────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"

SYSTEM   = platform.system()
IS_MACOS = SYSTEM == "Darwin"
IS_LINUX = SYSTEM == "Linux"

APP_NAME    = "nexus"
DISPLAY     = "Nexus — Central Hub"
PORT        = 5400
APP_DIR     = Path(__file__).parent.resolve()
MAIN_SCRIPT = str(APP_DIR / "app.py")


def _print(msg: str, colour: str = RESET) -> None:
    print(f"{colour}{msg}{RESET}", flush=True)

def ok(msg: str)   -> None: _print(f"[+] {msg}", GREEN)
def info(msg: str) -> None: _print(f"[*] {msg}", CYAN)
def warn(msg: str) -> None: _print(f"[~] {msg}", YELLOW)
def err(msg: str)  -> None: _print(f"[!] {msg}", RED)

def run_silent(cmd: list, *, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env=os.environ.copy(),
    )

def which(name: str) -> str | None:
    return shutil.which(name)


# ─────────────────────────────────────────────────────────────────────────────
# Python packages
# ─────────────────────────────────────────────────────────────────────────────

PIP_PACKAGES: list[tuple[str, str]] = [
    # Flask — Armin Ronacher @mitsuhiko — https://github.com/pallets/flask
    ("flask>=3.0.0",           "Flask web framework — Nexus server"),
    # Flask-SocketIO — Miguel Grinberg @miguelgrinberg — https://github.com/miguelgrinberg/Flask-SocketIO
    ("flask-socketio>=5.3.0",  "WebSocket real-time push — live instance updates"),
    # eventlet — eventlet contributors — https://github.com/eventlet/eventlet
    ("eventlet>=0.35.0",       "Async networking for Flask-SocketIO"),
    # Requests — Kenneth Reitz @psf — https://github.com/psf/requests
    ("requests>=2.28.0",       "HTTP client — queries /api/nexus-status on discovered apps"),
    # Werkzeug — Pallets team — https://github.com/pallets/werkzeug
    ("werkzeug>=3.0.0",        "Password hashing (generate_password_hash / check_password_hash)"),
]


# ─────────────────────────────────────────────────────────────────────────────
# System packages
# ─────────────────────────────────────────────────────────────────────────────

APT_PACKAGES: list[tuple[str, str]] = [
    ("openssl",       "TLS certificate generation (self-signed RSA-4096)"),
    ("python3-pip",   "Python 3 pip"),
    ("python3-dev",   "Python 3 headers"),
    ("git",           "Git version control"),
]

BREW_PACKAGES: list[tuple[str, str]] = [
    ("openssl", "TLS certificate generation"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Privilege helpers
# ─────────────────────────────────────────────────────────────────────────────

def require_root() -> None:
    if os.geteuid() != 0:
        err("This installer must be run as root or via sudo.")
        err("    sudo python3 install.py")
        sys.exit(1)
    info("Running as root — OK")


def _brew_cmd() -> str | None:
    """Return path to brew, checking common non-root install locations."""
    for candidate in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]:
        if os.path.isfile(candidate):
            return candidate
    return which("brew")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: pip
# ─────────────────────────────────────────────────────────────────────────────

def stage_pip(check_mode: bool = False) -> None:
    _print("\n" + "─" * 60, BOLD)
    _print("Stage: Python packages (pip)", BOLD)
    _print("─" * 60, BOLD)
    for spec, desc in PIP_PACKAGES:
        if check_mode:
            info(f"  [check] would pip install: {spec}  — {desc}")
            continue
        info(f"pip install {spec}  — {desc}")
        cmd = [sys.executable, "-m", "pip", "install", "--quiet",
               "--break-system-packages", spec]
        r = run_silent(cmd)
        if r.returncode == 0:
            ok(f"  {spec} installed")
        else:
            warn(f"  {spec} failed: {r.stderr.strip()[:120]}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: system packages
# ─────────────────────────────────────────────────────────────────────────────

def stage_system(check_mode: bool = False) -> None:
    _print("\n" + "─" * 60, BOLD)
    _print("Stage: system packages", BOLD)
    _print("─" * 60, BOLD)

    if IS_LINUX:
        apt = which("apt-get") or which("apt")
        if not apt:
            warn("apt/apt-get not found — skipping system packages.")
            return
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        if not check_mode:
            info("Running apt update ...")
            subprocess.run([apt, "update"], env=env, timeout=120)
        for pkg, desc in APT_PACKAGES:
            if check_mode:
                info(f"  [check] would apt install: {pkg}  — {desc}")
                continue
            info(f"apt install {pkg}  — {desc}")
            r = subprocess.run(
                [apt, "install", "-y", "-q", pkg,
                 "-o", "Dpkg::Options::=--force-confold"],
                env=env, timeout=180,
            )
            if r.returncode == 0:
                ok(f"  {pkg} installed")
            else:
                warn(f"  {pkg} returned code {r.returncode}")

    elif IS_MACOS:
        brew = _brew_cmd()
        if not brew:
            warn("brew not found — install Homebrew first: https://brew.sh")
            return
        _brew_user = os.environ.get("SUDO_USER", "")
        _brew_pfx  = ["sudo", "-u", _brew_user] if _brew_user and _brew_user != "root" else []
        for pkg, desc in BREW_PACKAGES:
            if check_mode:
                info(f"  [check] would brew install: {pkg}  — {desc}")
                continue
            info(f"brew install {pkg}  — {desc}")
            r = subprocess.run(_brew_pfx + [brew, "install", pkg], timeout=300)
            if r.returncode == 0:
                ok(f"  {pkg} installed")
            else:
                warn(f"  {pkg} brew install returned code {r.returncode}")
    else:
        warn(f"Unsupported platform: {SYSTEM}")


# ─────────────────────────────────────────────────────────────────────────────
# Stage: service
# ─────────────────────────────────────────────────────────────────────────────

def _systemd_unit() -> str:
    python_bin = which("python3") or sys.executable
    return f"""[Unit]
Description={DISPLAY} (port {PORT})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory={APP_DIR}
ExecStart={python_bin} {MAIN_SCRIPT}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""


def _launchd_plist(label: str, python_bin: str) -> str:
    log_dir = "/var/log/pr0t31n"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>{MAIN_SCRIPT}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{APP_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/{APP_NAME}.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/{APP_NAME}.err</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
"""


def stage_service(check_mode: bool = False) -> None:
    _print("\n" + "─" * 60, BOLD)
    _print("Stage: boot service", BOLD)
    _print("─" * 60, BOLD)

    if check_mode:
        if IS_LINUX:
            info(f"  [check] would install /etc/systemd/system/pr0t31n-{APP_NAME}.service")
        elif IS_MACOS:
            info(f"  [check] would install /Library/LaunchDaemons/com.pr0t31n.{APP_NAME}.plist")
        return

    python_bin = which("python3") or sys.executable

    if IS_LINUX:
        service_name = f"pr0t31n-{APP_NAME}"
        svc_path = Path(f"/etc/systemd/system/{service_name}.service")
        try:
            svc_path.write_text(_systemd_unit())
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "enable", "--now", service_name], check=False)
            ok(f"Systemd service  {service_name}  enabled and started  (port {PORT})")
            ok(f"  Logs: journalctl -u {service_name} -f")
        except PermissionError:
            warn("Cannot write to /etc/systemd — run as root to install service.")

    elif IS_MACOS:
        label = f"com.pr0t31n.{APP_NAME}"
        log_dir = Path("/var/log/pr0t31n")
        plist_path = Path(f"/Library/LaunchDaemons/{label}.plist")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            # Unload any existing instance first
            subprocess.run(["launchctl", "unload", str(plist_path)],
                           capture_output=True, check=False)
            plist_path.write_text(_launchd_plist(label, python_bin))
            subprocess.run(["launchctl", "load", "-w", str(plist_path)], check=False)
            ok(f"LaunchDaemon  {label}  loaded  (port {PORT})")
            ok(f"  Stdout: {log_dir}/{APP_NAME}.log")
            ok(f"  Stderr: {log_dir}/{APP_NAME}.err")
        except PermissionError:
            warn("Cannot write to /Library/LaunchDaemons — run as root.")
    else:
        warn(f"Service installation not supported on {SYSTEM}.")


def stage_uninstall() -> None:
    _print("\n" + "─" * 60, BOLD)
    _print("Stage: uninstall service", BOLD)
    _print("─" * 60, BOLD)

    if IS_LINUX:
        service_name = f"pr0t31n-{APP_NAME}"
        svc_path = Path(f"/etc/systemd/system/{service_name}.service")
        subprocess.run(["systemctl", "disable", "--now", service_name], check=False)
        if svc_path.exists():
            svc_path.unlink()
            ok(f"Removed {svc_path}")
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        ok(f"Service {service_name} removed.")

    elif IS_MACOS:
        label = f"com.pr0t31n.{APP_NAME}"
        plist_path = Path(f"/Library/LaunchDaemons/{label}.plist")
        subprocess.run(["launchctl", "unload", str(plist_path)],
                       capture_output=True, check=False)
        if plist_path.exists():
            plist_path.unlink()
            ok(f"Removed {plist_path}")
        ok(f"LaunchDaemon {label} removed.")
    else:
        warn(f"Uninstall not supported on {SYSTEM}.")


# ─────────────────────────────────────────────────────────────────────────────
# Verification summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary() -> None:
    _verify_python = [
        ("flask",         "flask"),
        ("flask-socketio","flask_socketio"),
        ("eventlet",      "eventlet"),
        ("requests",      "requests"),
        ("werkzeug",      "werkzeug"),
    ]
    _verify_bins = ["python3", "openssl"]

    _print("\n" + "─" * 60, BOLD)
    _print("Post-install verification", BOLD)
    _print("─" * 60, BOLD)

    _print("\nSystem binaries:", BOLD)
    for binary in _verify_bins:
        path = which(binary)
        status = f"{GREEN}✓{RESET}" if path else f"{YELLOW}✗{RESET}"
        print(f"  {status}  {binary:<18} {path or '(not found)'}")

    _print("\nPython packages:", BOLD)
    for pkg_name, module in _verify_python:
        r = run_silent([sys.executable, "-c", f"import {module}"])
        found = r.returncode == 0
        status = f"{GREEN}✓{RESET}" if found else f"{YELLOW}✗{RESET}"
        print(f"  {status}  {pkg_name}")

    _print("\nService status:", BOLD)
    if IS_LINUX:
        service_name = f"pr0t31n-{APP_NAME}"
        r = run_silent(["systemctl", "is-active", service_name])
        active = r.stdout.strip() == "active"
        status = f"{GREEN}✓ active{RESET}" if active else f"{YELLOW}✗ {r.stdout.strip()}{RESET}"
        print(f"  {status}  systemd/{service_name}")
    elif IS_MACOS:
        label = f"com.pr0t31n.{APP_NAME}"
        plist_path = Path(f"/Library/LaunchDaemons/{label}.plist")
        exists = plist_path.exists()
        status = f"{GREEN}✓ installed{RESET}" if exists else f"{YELLOW}✗ not installed{RESET}"
        print(f"  {status}  {label}")

    _print(f"\nNexus is available at:", BOLD)
    print(f"  https://localhost:{PORT}")
    print(f"  Default credentials: admin / t3mpPas$w0rd  (must change on first login)")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Nexus installer — Python deps and boot service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--check",         action="store_true",
                   help="Dry run — show what would be installed")
    p.add_argument("--skip-python",   action="store_true",
                   help="Skip pip install stage")
    p.add_argument("--skip-system",   action="store_true",
                   help="Skip system package install stage")
    p.add_argument("--skip-service",  action="store_true",
                   help="Skip service registration")
    p.add_argument("--uninstall",     action="store_true",
                   help="Remove the boot service (does not remove pip packages)")
    return p.parse_args()


def main() -> None:
    _print("=" * 60, BOLD)
    _print(f"  {DISPLAY} — Installer", BOLD)
    _print(f"  Platform : {SYSTEM}", BOLD)
    _print(f"  Port     : {PORT}", BOLD)
    _print(f"  App dir  : {APP_DIR}", BOLD)
    _print("=" * 60, BOLD)

    args = parse_args()

    if args.uninstall:
        require_root()
        stage_uninstall()
        return

    require_root()

    if not args.skip_system:
        stage_system(check_mode=args.check)

    if not args.skip_python:
        stage_pip(check_mode=args.check)

    if not args.skip_service:
        stage_service(check_mode=args.check)

    if not args.check:
        print_summary()
    else:
        info("Dry run complete — no changes made.")


if __name__ == "__main__":
    main()
