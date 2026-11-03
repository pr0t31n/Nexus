"""macOS service installer — launchd plist for nexus-agent."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LABEL = "com.pr0t31n.nexus-agent"
_PLIST_PATH = Path(f"/Library/LaunchDaemons/{_LABEL}.plist")

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{agent_py}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{work_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{work_dir}/agent.log</string>
    <key>StandardErrorPath</key>
    <string>{work_dir}/agent.log</string>
</dict>
</plist>
"""


def install(install_dir: Path) -> bool:
    agent_py = install_dir / "agent.py"
    if not agent_py.exists():
        print(f"ERROR: {agent_py} not found")
        return False

    content = _PLIST_TEMPLATE.format(
        label=_LABEL,
        python=sys.executable,
        agent_py=str(agent_py),
        work_dir=str(install_dir),
    )
    _PLIST_PATH.write_text(content)
    subprocess.run(["launchctl", "load", "-w", str(_PLIST_PATH)], check=True)
    print(f"Launch daemon {_LABEL} installed and loaded")
    return True


def uninstall() -> bool:
    if _PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], check=False)
        _PLIST_PATH.unlink()
    print(f"Launch daemon {_LABEL} removed")
    return True


def status() -> str:
    r = subprocess.run(
        ["launchctl", "list"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines():
        if _LABEL in line:
            parts = line.split()
            pid = parts[0] if parts else "-"
            return "running" if pid != "-" else "stopped"
    return "not_installed"
