"""Nexus Agent — command executor with streaming output."""
from __future__ import annotations

import logging
import os
import platform
import shutil
import signal
import subprocess
import threading
from typing import Callable

log = logging.getLogger("nexus_agent.executor")

_TOOL_INSTALL_MAP: dict[str, str] = {
    "crucible":     "/opt/pr0t31n/Crucible/install.py",
    "gauntlet":     "/opt/pr0t31n/Gauntlet/install.py",
    "bastion":      "/opt/pr0t31n/Bastion/install.py",
    "fracture":     "/opt/pr0t31n/Fracture/install.py",
    "siphon":       "/opt/pr0t31n/Siphon/install.py",
    "scattershot3": "/opt/pr0t31n/Scattershot3/install.py",
    "osift":        "/opt/pr0t31n/OSift/install.py",
    "gearbox":      "/opt/pr0t31n/Gearbox/install.py",
}


def _tool_dir(tool_name: str) -> str:
    script = _TOOL_INSTALL_MAP.get(tool_name, "")
    if script:
        return str(os.path.dirname(script))
    return f"/opt/pr0t31n/{tool_name.capitalize()}"


def _service_name(tool_name: str) -> str:
    return f"pr0t31n-{tool_name}"


class Executor:

    def __init__(self, allowed_tools: list[str] | None = None):
        self._allowed = set(allowed_tools or [])
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen] = {}

    def execute(self, cmd_id: str, cmd: str, timeout: int = 300,
                on_output: Callable[[str], None] | None = None) -> dict:
        output_buf = []
        exit_code = -1
        try:
            proc = subprocess.Popen(
                cmd, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            with self._lock:
                self._active[cmd_id] = proc

            try:
                for line in proc.stdout:
                    output_buf.append(line)
                    if on_output:
                        try:
                            on_output(line)
                        except Exception:
                            pass
                proc.wait(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                output_buf.append("\n[TIMEOUT] Command timed out\n")
                exit_code = -1

        except Exception as exc:
            output_buf.append(f"\n[ERROR] {exc}\n")
        finally:
            with self._lock:
                self._active.pop(cmd_id, None)

        return {
            "exit_code": exit_code,
            "output": "".join(output_buf),
        }

    def cancel(self, cmd_id: str):
        with self._lock:
            proc = self._active.pop(cmd_id, None)
        if proc:
            try:
                if platform.system() != "Windows":
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def arsenal_op(self, operation: str, tool_name: str) -> str | None:
        if self._allowed and tool_name not in self._allowed:
            return None
        d = _tool_dir(tool_name)
        svc = _service_name(tool_name)
        cmds = {
            "install": f"cd {d} && python3 install.py 2>&1",
            "update":  f"cd {d} && git pull 2>&1 && python3 install.py --skip-system 2>&1",
            "status":  f"cd {d} && python3 install.py --check 2>&1",
            "restart": f"systemctl restart {svc} 2>&1 || launchctl kickstart -k system/com.pr0t31n.{tool_name} 2>&1",
            "version": f"cat {d}/VERSION 2>/dev/null || echo unknown",
        }
        return cmds.get(operation)

    def get_system_info(self) -> dict:
        info = {
            "hostname": platform.node(),
            "os_family": platform.system().lower(),
            "arch": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count() or 0,
        }
        try:
            import psutil
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            info["memory_total_mb"] = int(mem.total / 1048576)
            info["memory_used_pct"] = int(mem.percent)
            info["disk_total_gb"] = int(disk.total / 1073741824)
            info["disk_used_pct"] = int(disk.used / disk.total * 100)
        except ImportError:
            pass
        return info
