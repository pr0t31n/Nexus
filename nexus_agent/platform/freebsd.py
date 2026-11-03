"""FreeBSD service installer — rc.d script for nexus-agent."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SVC_NAME = "nexus_agent"
_RC_PATH = Path(f"/usr/local/etc/rc.d/{_SVC_NAME}")

_RC_TEMPLATE = """\
#!/bin/sh

# PROVIDE: {svc}
# REQUIRE: NETWORKING
# KEYWORD: shutdown

. /etc/rc.subr

name="{svc}"
rcvar="{svc}_enable"
pidfile="/var/run/${{name}}.pid"
command="{python}"
command_args="{agent_py} &"

load_rc_config $name
: ${{{svc}_enable:="NO"}}

run_rc_command "$1"
"""


def install(install_dir: Path) -> bool:
    agent_py = install_dir / "agent.py"
    if not agent_py.exists():
        print(f"ERROR: {agent_py} not found")
        return False

    content = _RC_TEMPLATE.format(
        svc=_SVC_NAME,
        python=sys.executable,
        agent_py=str(agent_py),
    )
    _RC_PATH.write_text(content)
    _RC_PATH.chmod(0o755)

    rc_conf = Path("/etc/rc.conf")
    enable_line = f'{_SVC_NAME}_enable="YES"\n'
    text = rc_conf.read_text() if rc_conf.exists() else ""
    if enable_line.strip() not in text:
        with open(rc_conf, "a") as f:
            f.write(enable_line)

    subprocess.run(["service", _SVC_NAME, "start"], check=True)
    print(f"rc.d service {_SVC_NAME} installed and started")
    return True


def uninstall() -> bool:
    subprocess.run(["service", _SVC_NAME, "stop"], check=False)
    if _RC_PATH.exists():
        _RC_PATH.unlink()
    rc_conf = Path("/etc/rc.conf")
    if rc_conf.exists():
        lines = rc_conf.read_text().splitlines(keepends=True)
        lines = [l for l in lines if _SVC_NAME not in l]
        rc_conf.write_text("".join(lines))
    print(f"rc.d service {_SVC_NAME} removed")
    return True


def status() -> str:
    r = subprocess.run(
        ["service", _SVC_NAME, "status"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and "running" in r.stdout.lower():
        return "running"
    return "stopped" if _RC_PATH.exists() else "not_installed"
