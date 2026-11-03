#!/usr/bin/env python3
"""Nexus Agent installer — multi-platform."""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

TOOL_NAME = "Nexus Agent"
MIN_PYTHON = (3, 10)
INSTALL_DIR = Path("/opt/pr0t31n/nexus-agent")


def _run(cmd, check=True):
    print(f"  → {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=check)


def _has(binary):
    return shutil.which(binary) is not None


def install_pip_deps():
    reqs = Path(__file__).resolve().parent / "requirements.txt"
    if reqs.exists():
        _run([sys.executable, "-m", "pip", "install",
              "--break-system-packages", "-r", str(reqs)])


def install_service():
    sysname = platform.system().lower()
    if sysname == "linux":
        from platform.linux import install as svc_install
    elif sysname == "darwin":
        from platform.macos import install as svc_install
    elif sysname == "windows":
        from platform.windows import install as svc_install
    elif "freebsd" in sysname:
        from platform.freebsd import install as svc_install
    else:
        print(f"WARNING: No service installer for {sysname}")
        return
    svc_install(INSTALL_DIR)


def deploy_files():
    src = Path(__file__).resolve().parent
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in ("__pycache__", ".git"):
            continue
        dest = INSTALL_DIR / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    print(f"  Files deployed to {INSTALL_DIR}")


def main():
    if sys.version_info < MIN_PYTHON:
        print(f"ERROR: Python {'.'.join(map(str, MIN_PYTHON))}+ required")
        sys.exit(1)

    print(f"\n{TOOL_NAME} — Installation\n")

    print("[1/3] Installing Python dependencies...")
    install_pip_deps()

    print("[2/3] Deploying agent files...")
    deploy_files()

    print("[3/3] Installing system service...")
    try:
        install_service()
    except Exception as exc:
        print(f"WARNING: Service install failed: {exc}")
        print("  You can run the agent manually: python3 agent.py")

    print(f"\n{TOOL_NAME} installed at {INSTALL_DIR}")
    print("  Configure: edit config.yaml with your broker URL and certificates")
    print("  Manual start: python3 agent.py [config.yaml]")


if __name__ == "__main__":
    main()
