#!/usr/bin/env python3
"""Nexus Agent — connects to broker, executes commands, reports heartbeats."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import signal
import socket
import ssl
import sys
import time
from pathlib import Path

try:
    import websockets
except ImportError:
    websockets = None

from executor import Executor

log = logging.getLogger("nexus_agent")

_RECONNECT_DELAYS = [5, 10, 30, 60]


def _load_config(path: str = "config.yaml") -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        log.error("Config file not found: %s", path)
        sys.exit(1)
    try:
        import yaml
        return yaml.safe_load(cfg_path.read_text()) or {}
    except ImportError:
        pass
    data = {}
    for line in cfg_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
            elif v.isdigit():
                v = int(v)
            data[k] = v
    return data


def _detect_os() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "windows":
        return "windows"
    if "freebsd" in s:
        return "freebsd"
    return "linux"


def _detect_arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m.startswith("armv7"):
        return "armv7l"
    return m


def _detect_fabrics() -> list[dict]:
    fabrics = []
    try:
        import netifaces
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            for af in (netifaces.AF_INET,):
                for addr_info in addrs.get(af, []):
                    ip = addr_info.get("addr", "")
                    if ip and not ip.startswith("127."):
                        fabrics.append({"name": iface, "address": ip})
    except ImportError:
        pass
    return fabrics


class Agent:

    def __init__(self, config: dict):
        self._config = config
        self._broker_url = config.get("broker_url", "wss://localhost:5401/ws")
        self._heartbeat_interval = int(config.get("heartbeat_interval", 30))
        self._hostname = config.get("hostname_override", "") or socket.gethostname()
        self._os_family = _detect_os()
        self._arch = _detect_arch()
        self._allowed_tools = config.get("allowed_tools", [])
        self._executor = Executor(allowed_tools=self._allowed_tools)
        self._running = True
        self._ws = None

        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, sig, frame):
        log.info("Received signal %s, shutting down", sig)
        self._running = False

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert_file = self._config.get("cert_file", "")
        key_file = self._config.get("key_file", "")
        ca_file = self._config.get("ca_file", "")
        if not cert_file or not Path(cert_file).exists():
            return None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
        if ca_file and Path(ca_file).exists():
            ctx.load_verify_locations(cafile=ca_file)
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def run(self):
        attempt = 0
        while self._running:
            try:
                ssl_ctx = self._build_ssl_context()
                async with websockets.connect(
                    self._broker_url, ssl=ssl_ctx,
                    ping_interval=20, ping_timeout=20,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    log.info("Connected to broker at %s", self._broker_url)
                    await self._register(ws)
                    await self._message_loop(ws)
            except Exception as exc:
                log.warning("Connection failed: %s", exc)

            self._ws = None
            if not self._running:
                break
            delay = _RECONNECT_DELAYS[min(attempt, len(_RECONNECT_DELAYS) - 1)]
            log.info("Reconnecting in %ds (attempt %d)", delay, attempt + 1)
            await asyncio.sleep(delay)
            attempt += 1

    async def _register(self, ws):
        msg = {
            "type": "register",
            "hostname": self._hostname,
            "os_family": self._os_family,
            "arch": self._arch,
            "agent_version": _get_version(),
            "fabrics": _detect_fabrics(),
            "allowed_tools": self._allowed_tools,
        }
        await ws.send(json.dumps(msg))

    async def _message_loop(self, ws):
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
        try:
            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_type = msg.get("type", "")
                if msg_type == "command":
                    asyncio.create_task(self._handle_command(ws, msg))
                elif msg_type == "cancel":
                    self._executor.cancel(msg.get("id", ""))
        finally:
            heartbeat_task.cancel()

    async def _heartbeat_loop(self, ws):
        while self._running:
            try:
                await ws.send(json.dumps({"type": "heartbeat"}))
            except Exception:
                break
            await asyncio.sleep(self._heartbeat_interval)

    async def _handle_command(self, ws, msg: dict):
        cmd_id = msg.get("id", "")
        cmd = msg.get("cmd", "")
        timeout = msg.get("timeout", 300)

        def on_output(chunk: str):
            asyncio.run_coroutine_threadsafe(
                ws.send(json.dumps({"type": "output", "id": cmd_id, "chunk": chunk})),
                asyncio.get_event_loop(),
            )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._executor.execute, cmd_id, cmd, timeout, on_output,
        )

        await ws.send(json.dumps({
            "type": "complete",
            "id": cmd_id,
            "exit_code": result.get("exit_code", -1),
            "output": result.get("output", ""),
        }))


def _get_version() -> str:
    vf = Path(__file__).resolve().parent / "VERSION"
    if vf.exists():
        return vf.read_text().strip()
    return "0.0.0"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = _load_config(cfg_path)
    agent = Agent(config)
    log.info("Nexus agent v%s starting (%s/%s)", _get_version(),
             agent._os_family, agent._arch)
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
