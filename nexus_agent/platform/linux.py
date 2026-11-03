"""Linux service installer — systemd unit for nexus-agent."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_UNIT_NAME = "nexus-agent"
_UNIT_PATH = Path(f"/etc/systemd/system/{_UNIT_NAME}.service")

_UNIT_TEMPLATE = """\
[Unit]
Description=Nexus Fleet Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={work_dir}
ExecStart={python} {agent_py}
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""


def install(install_dir: Path) -> bool:
    agent_py = install_dir / "agent.py"
    if not agent_py.exists():
        print(f"ERROR: {agent_py} not found")
        return False

    content = _UNIT_TEMPLATE.format(
        user=os.environ.get("SUDO_USER", os.getlogin()),
        work_dir=str(install_dir),
        python=sys.executable,
        agent_py=str(agent_py),
    )
    _UNIT_PATH.write_text(content)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", _UNIT_NAME], check=True)
    subprocess.run(["systemctl", "start", _UNIT_NAME], check=True)
    print(f"Service {_UNIT_NAME} installed and started")
    return True


def uninstall() -> bool:
    subprocess.run(["systemctl", "stop", _UNIT_NAME], check=False)
    subprocess.run(["systemctl", "disable", _UNIT_NAME], check=False)
    if _UNIT_PATH.exists():
        _UNIT_PATH.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print(f"Service {_UNIT_NAME} removed")
    return True


def status() -> str:
    r = subprocess.run(
        ["systemctl", "is-active", _UNIT_NAME],
        capture_output=True, text=True,
    )
    return r.stdout.strip()
