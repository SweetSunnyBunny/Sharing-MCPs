from __future__ import annotations

import atexit
import os
import socket
import subprocess
from pathlib import Path
from typing import Any


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


class SidecarManager:
    def __init__(self) -> None:
        self.playwright_enabled = os.environ.get("MACHINE_AGENT_MANAGE_PLAYWRIGHT", "1") != "0"
        self.playwright_port = int(os.environ.get("PLAYWRIGHT_MCP_PORT", "8794"))
        self._playwright_process: subprocess.Popen[str] | None = None
        self._logs_dir = Path(__file__).resolve().parent / "logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        atexit.register(self.shutdown)

    def ensure_started(self) -> None:
        if self.playwright_enabled:
            self._ensure_playwright()

    def health(self) -> dict[str, Any]:
        playwright_listening = _is_port_open("127.0.0.1", self.playwright_port)
        playwright_running = (
            self._playwright_process is not None
            and self._playwright_process.poll() is None
        )
        return {
            "playwright": {
                "enabled": self.playwright_enabled,
                "port": self.playwright_port,
                "listening": playwright_listening,
                "managed_by_agent": self._playwright_process is not None,
                "running": playwright_running or playwright_listening,
            }
        }

    def shutdown(self) -> None:
        if self._playwright_process is not None and self._playwright_process.poll() is None:
            self._playwright_process.terminate()

    def _ensure_playwright(self) -> None:
        if _is_port_open("127.0.0.1", self.playwright_port):
            return

        stdout_path = self._logs_dir / "playwright.out.log"
        stderr_path = self._logs_dir / "playwright.err.log"

        stdout_handle = open(stdout_path, "a", encoding="utf-8")
        stderr_handle = open(stderr_path, "a", encoding="utf-8")

        self._playwright_process = subprocess.Popen(
            [
                "cmd",
                "/c",
                "npx",
                "@playwright/mcp@latest",
                "--browser",
                "chrome",
                "--port",
                str(self.playwright_port),
            ],
            cwd=Path(__file__).resolve().parent,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
        )


sidecars = SidecarManager()
