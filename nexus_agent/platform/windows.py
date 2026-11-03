"""Windows service installer — NSSM or schtasks for nexus-agent."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SVC_NAME = "NexusAgent"


def install(install_dir: Path) -> bool:
    agent_py = install_dir / "agent.py"
    if not agent_py.exists():
        print(f"ERROR: {agent_py} not found")
        return False

    if shutil.which("nssm"):
        return _install_nssm(install_dir, agent_py)
    return _install_schtask(install_dir, agent_py)


def _install_nssm(install_dir: Path, agent_py: Path) -> bool:
    subprocess.run(["nssm", "install", _SVC_NAME, sys.executable, str(agent_py)], check=True)
    subprocess.run(["nssm", "set", _SVC_NAME, "AppDirectory", str(install_dir)], check=True)
    subprocess.run(["nssm", "set", _SVC_NAME, "DisplayName", "Nexus Fleet Agent"], check=True)
    subprocess.run(["nssm", "set", _SVC_NAME, "Start", "SERVICE_AUTO_START"], check=True)
    subprocess.run(["nssm", "start", _SVC_NAME], check=True)
    print(f"NSSM service {_SVC_NAME} installed and started")
    return True


def _install_schtask(install_dir: Path, agent_py: Path) -> bool:
    cmd = f'"{sys.executable}" "{agent_py}"'
    subprocess.run([
        "schtasks", "/Create", "/TN", _SVC_NAME,
        "/TR", cmd, "/SC", "ONSTART", "/RU", "SYSTEM", "/F",
    ], check=True)
    subprocess.run(["schtasks", "/Run", "/TN", _SVC_NAME], check=True)
    print(f"Scheduled task {_SVC_NAME} created and started")
    return True


def uninstall() -> bool:
    if shutil.which("nssm"):
        subprocess.run(["nssm", "stop", _SVC_NAME], check=False)
        subprocess.run(["nssm", "remove", _SVC_NAME, "confirm"], check=False)
    else:
        subprocess.run(["schtasks", "/End", "/TN", _SVC_NAME], check=False)
        subprocess.run(["schtasks", "/Delete", "/TN", _SVC_NAME, "/F"], check=False)
    print(f"Service {_SVC_NAME} removed")
    return True


def status() -> str:
    if shutil.which("nssm"):
        r = subprocess.run(
            ["nssm", "status", _SVC_NAME],
            capture_output=True, text=True,
        )
        return r.stdout.strip().lower() if r.returncode == 0 else "not_installed"
    r = subprocess.run(
        ["schtasks", "/Query", "/TN", _SVC_NAME],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return "not_installed"
    return "running" if "Running" in r.stdout else "stopped"
