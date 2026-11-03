#!/usr/bin/env python3
"""
Nexus — Hub Flask Web Application
Install script for /opt/pr0t31n/Nexus/
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ─── ANSI colours ────────────────────────────────────────────────────────────
R  = "\033[1;31m"
G  = "\033[1;32m"
Y  = "\033[1;33m"
B  = "\033[1;34m"
C  = "\033[1;36m"
W  = "\033[0m"
# ─────────────────────────────────────────────────────────────────────────────

APP_NAME     = "Nexus"
APP_DIR      = Path("/opt/pr0t31n/Nexus")
SERVICE_NAME = "pr0t31n-nexus"
APP_PORT     = 6000

SYSTEMD_UNIT = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
LAUNCHD_PLIST = Path(f"/Library/LaunchDaemons/com.pr0t31n.nexus.plist")

# system package lists --------------------------------------------------------
APT_PKGS    = ["python3-pip", "python3-dev", "git", "openssl",
               "libssl-dev", "nmap"]
DNF_PKGS    = ["python3-pip", "python3-devel", "git", "openssl",
               "openssl-devel", "nmap"]
ZYPPER_PKGS = ["python3-pip", "python3-devel", "git", "openssl",
               "libopenssl-devel", "nmap"]
BREW_PKGS   = ["openssl", "nmap", "git"]

# pip packages ----------------------------------------------------------------
PIP_PKGS = [
    "flask>=3.0.0",
    "flask-socketio>=5.3.0",
    "requests>=2.28.0",
    "cryptography>=41.0.0",
    "paramiko>=3.4.0",
    "sshtunnel>=0.4.0",
]

# ─── helpers ─────────────────────────────────────────────────────────────────

def banner():
    print(f"""
{C}╔══════════════════════════════════════════════╗
║      Nexus — Network Fabric System           ║
║        Install Script                        ║
╚══════════════════════════════════════════════╝{W}
""")


def info(msg):  print(f"{G}[+]{W} {msg}")
def warn(msg):  print(f"{Y}[!]{W} {msg}")
def note(msg):  print(f"{Y}[i]{W} {msg}")
def err(msg):   print(f"{R}[✗]{W} {msg}")
def step(msg):  print(f"\n{B}━━ {msg} ━━{W}")


def run(cmd, **kwargs):
    print(f"    {C}>{W} {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result


def run_ok(cmd, **kwargs):
    try:
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       **kwargs)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def require_root():
    if platform.system() != "Darwin" and os.geteuid() != 0:
        err("This script must be run as root (or with sudo) on Linux.")
        sys.exit(1)


def detect_os():
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Windows":
        return "windows"
    os_release = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    os_release[k] = v.strip('"')
    except FileNotFoundError:
        pass
    dist_id = os_release.get("ID", "").lower()
    id_like  = os_release.get("ID_LIKE", "").lower()
    if dist_id in ("kali", "ubuntu", "debian") or "debian" in id_like:
        return "apt"
    if dist_id in ("rhel", "centos", "rocky", "almalinux", "fedora") or "rhel" in id_like or "fedora" in id_like:
        return "dnf"
    if dist_id in ("sles", "opensuse", "opensuse-leap", "opensuse-tumbleweed") or "suse" in id_like:
        return "zypper"
    return "apt"


def _pip_installed(pkg_spec):
    import importlib.util
    name = pkg_spec.split(">=")[0].split("==")[0].split("[")[0].split("<")[0].strip()
    _IMPORT_MAP = {
        "flask-socketio": "flask_socketio", "python-engineio": "engineio",
        "python-socketio": "socketio",
    }
    mod = _IMPORT_MAP.get(name, name.replace("-", "_"))
    return importlib.util.find_spec(mod) is not None


def _brew_installed_set():
    try:
        r = subprocess.run(["brew", "list", "--formula", "-1"],
                           capture_output=True, text=True, check=True)
        return set(r.stdout.strip().splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()


def _brew_install(pkgs):
    brew_path = shutil.which("brew")
    if not brew_path:
        note("Homebrew not found; skipping brew installs.")
        return
    installed = _brew_installed_set()
    SUDO_USER = os.environ.get("SUDO_USER", "")
    for pkg in pkgs:
        if pkg in installed:
            info(f"  ✓ {pkg} already installed")
            continue
        if os.getuid() == 0:
            if not SUDO_USER:
                note(f"Running as root with no SUDO_USER — cannot brew install {pkg}; skipping.")
                continue
            run(["sudo", "-u", SUDO_USER, brew_path, "install", pkg])
        else:
            run([brew_path, "install", pkg])


# ─── phase 1: system packages ────────────────────────────────────────────────

def install_system_packages(os_type, args):
    step("Phase 1 — System Packages")
    if args.skip_apt:
        note("--skip-system set; skipping system package install.")
        return

    if os_type == "windows":
        warn("Windows detected. Please install Python 3, git, openssl, and nmap manually.")
        return

    if os_type == "macos":
        _brew_install(BREW_PKGS)
        return

    if os_type == "apt":
        info("Running apt update + apt upgrade …")
        run(["apt-get", "update", "-y"])
        run(["apt-get", "upgrade", "-y"])
        info("Installing APT packages …")
        run(["apt-get", "install", "-y"] + APT_PKGS)

    elif os_type == "dnf":
        info("Installing DNF packages …")
        run(["dnf", "install", "-y"] + DNF_PKGS)

    elif os_type == "zypper":
        info("Installing zypper packages …")
        run(["zypper", "install", "-y"] + ZYPPER_PKGS)


# ─── phase 2: pip packages ───────────────────────────────────────────────────

def _pip_cmd():
    """Return pip invocation as a list."""
    if shutil.which("pip3"):
        return ["pip3"]
    if shutil.which("pip"):
        return ["pip"]
    return [sys.executable, "-m", "pip"]


def install_pip_packages(args):
    step("Phase 2 — Pip Packages")
    if args.skip_pip:
        note("--skip-pip set; skipping.")
        return
    pip = _pip_cmd()
    for pkg in PIP_PKGS:
        if _pip_installed(pkg):
            info(f"  ✓ {pkg} already installed")
            continue
        info(f"pip install {pkg} …")
        try:
            run(pip + ["install", "--break-system-packages", pkg])
        except RuntimeError as exc:
            warn(f"pip install failed for {pkg}: {exc}")


# ─── phase 3: go (none for Nexus) ────────────────────────────────────────────

def install_go_tools(args):
    step("Phase 3 — Go Tools")
    if args.skip_go:
        note("--skip-go set; skipping.")
        return
    info("Nexus has no Go tool dependencies — phase skipped.")


# ─── phase 4: git (none for Nexus) ───────────────────────────────────────────

def install_git_tools(args):
    step("Phase 4 — Git Tools")
    if args.skip_git:
        note("--skip-git set; skipping.")
        return
    info("Nexus has no git-cloned tool dependencies — phase skipped.")


# ─── service files ────────────────────────────────────────────────────────────

def install_service_files(os_type):
    step("Installing Service Files")

    if os_type in ("apt", "dnf", "zypper"):
        _install_systemd()

    elif os_type == "macos":
        _install_launchd()

    elif os_type == "windows":
        note("Service file installation is not supported on Windows. "
             "Run Nexus manually with: python app.py")


def _install_systemd():
    info(f"Writing systemd unit → {SYSTEMD_UNIT}")
    unit_content = f"""\
[Unit]
Description=Nexus Hub Flask Application
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={APP_DIR}
ExecStart=/usr/bin/python3 {APP_DIR}/app.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
    SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT.write_text(unit_content)
    info("Reloading systemd daemon …")
    try:
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", SERVICE_NAME])
        info(f"Service {SERVICE_NAME} enabled. Start with: systemctl start {SERVICE_NAME}")
    except RuntimeError as exc:
        warn(f"systemctl command failed: {exc}")


def _install_launchd():
    info(f"Writing launchd plist → {LAUNCHD_PLIST}")
    python3 = shutil.which("python3") or "/usr/bin/python3"
    plist_content = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pr0t31n.nexus</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python3}</string>
        <string>{APP_DIR}/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{APP_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/var/log/pr0t31n-nexus.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/pr0t31n-nexus.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
"""
    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_text(plist_content)
    try:
        run(["launchctl", "load", "-w", str(LAUNCHD_PLIST)])
        info(f"launchd service loaded. Stop with: launchctl unload {LAUNCHD_PLIST}")
    except RuntimeError as exc:
        warn(f"launchctl load failed: {exc}")


# ─── check mode ──────────────────────────────────────────────────────────────

def run_check():
    step("Check Mode — Dependency Status")
    checks = [
        ("nmap",        shutil.which("nmap")),
        ("python3",     shutil.which("python3") or sys.executable),
        ("git",         shutil.which("git")),
        ("openssl",     shutil.which("openssl")),
    ]
    all_ok = True
    for name, path in checks:
        if path and Path(path).exists():
            info(f"{name:22s} {G}OK{W}  ({path})")
        else:
            err(f"{name:22s} {R}MISSING{W}")
            all_ok = False

    # Python modules
    for mod in ("flask", "flask_socketio", "requests", "paramiko", "sshtunnel"):
        ok = run_ok([sys.executable, "-c", f"import {mod}"])
        if ok:
            info(f"python:{mod:16s} {G}OK{W}")
        else:
            err(f"python:{mod:16s} {R}MISSING{W}")
            all_ok = False

    # Service file
    os_type = detect_os()
    if os_type in ("apt", "dnf", "zypper"):
        svc_ok = SYSTEMD_UNIT.exists()
        status = f"{G}OK{W}" if svc_ok else f"{Y}NOT INSTALLED{W}"
        print(f"  {'systemd unit':22s} {status}")
    elif os_type == "macos":
        svc_ok = LAUNCHD_PLIST.exists()
        status = f"{G}OK{W}" if svc_ok else f"{Y}NOT INSTALLED{W}"
        print(f"  {'launchd plist':22s} {status}")

    print()
    if all_ok:
        info(f"{G}All checks passed.{W}")
    else:
        warn(f"{Y}Some checks failed.{W}")
    return all_ok


# ─── post-install summary ────────────────────────────────────────────────────

def post_install_summary(os_type):
    step("Post-Install Verification Summary")
    results = []

    # Binaries
    for tool in ("nmap", "python3", "git", "openssl"):
        path = shutil.which(tool)
        results.append((tool, bool(path), path or "missing"))

    # Python modules
    for mod in ("flask", "flask_socketio", "requests", "paramiko", "sshtunnel"):
        ok = run_ok([sys.executable, "-c", f"import {mod}"])
        results.append((f"python:{mod}", ok, "installed" if ok else "missing"))

    # Service file
    if os_type in ("apt", "dnf", "zypper"):
        ok = SYSTEMD_UNIT.exists()
        results.append(("systemd unit", ok, str(SYSTEMD_UNIT) if ok else "not installed"))
    elif os_type == "macos":
        ok = LAUNCHD_PLIST.exists()
        results.append(("launchd plist", ok, str(LAUNCHD_PLIST) if ok else "not installed"))

    pad = max(len(r[0]) for r in results) + 2
    print()
    for label, ok, detail in results:
        status = f"{G}OK{W}" if ok else f"{R}FAIL{W}"
        print(f"  {label:<{pad}} {status}  {detail}")

    failed = [r for r in results if not r[1]]
    print()
    if not failed:
        info(f"{G}Nexus installation complete and verified.{W}")
        info(f"Nexus runs on port {APP_PORT}.")
        if os_type in ("apt", "dnf", "zypper"):
            info(f"Start with: {C}systemctl start {SERVICE_NAME}{W}")
        elif os_type == "macos":
            info(f"Service loaded via launchd at port {APP_PORT}.")
    else:
        warn(f"{Y}{len(failed)} item(s) need attention (see above).{W}")


# ─── main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description=f"Installer for {APP_NAME}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--skip-apt",    "--skip-system", dest="skip_apt",
                   action="store_true", help="Skip system package installation")
    p.add_argument("--skip-pip",    action="store_true",
                   help="Skip pip package installation")
    p.add_argument("--skip-go",     action="store_true",
                   help="Skip Go tools phase (no-op for Nexus)")
    p.add_argument("--skip-git",    action="store_true",
                   help="Skip git tools phase (no-op for Nexus)")
    p.add_argument("--check",       action="store_true",
                   help="Check dependencies without installing")
    return p.parse_args()


def main():
    banner()
    args = parse_args()
    os_type = detect_os()
    info(f"Detected OS type: {os_type}")

    if args.check:
        ok = run_check()
        sys.exit(0 if ok else 1)

    if os_type != "windows":
        require_root()

    # Phase 1 — system packages
    install_system_packages(os_type, args)

    # Phase 2 — pip
    install_pip_packages(args)

    # Phase 3 — Go (no-op)
    install_go_tools(args)

    # Phase 4 — Git (no-op)
    install_git_tools(args)

    # Service files
    if os_type != "windows":
        install_service_files(os_type)

    # Summary
    post_install_summary(os_type)


if __name__ == "__main__":
    main()
