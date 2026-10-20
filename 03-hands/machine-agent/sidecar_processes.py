"""Own optional helper processes needed by the local machine bridge.

Only processes started by this manager are terminated on shutdown. An existing
listener on the configured Playwright port is reused rather than replaced."""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
from pathlib import Path
from typing import Any


# Pinned on purpose. This used to be `@latest`, which downloads and runs whatever
# was published most recently, with the user's privileges, on every bridge start.
# Bump it deliberately (or set PLAYWRIGHT_MCP_VERSION) after checking a release.
PLAYWRIGHT_MCP_VERSION = os.environ.get("PLAYWRIGHT_MCP_VERSION", "0.0.83")


def _is_port_open(host: str, port: int) -> bool:
    # create_connection tries every address the name resolves to. Playwright MCP
    # binds "localhost", which on this machine is IPv6 (::1) only, so an
    # IPv4-only probe reported it as not listening and a second copy was spawned.
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


class SidecarManager:
    def __init__(self) -> None:
        self.playwright_enabled = os.environ.get("MACHINE_AGENT_MANAGE_PLAYWRIGHT", "1") != "0"
        self.playwright_port = int(os.environ.get("PLAYWRIGHT_MCP_PORT", "8794"))
        self._playwright_process: subprocess.Popen[str] | None = None
        self._log_handles: list[Any] = []
        self._logs_dir = Path(__file__).resolve().parent / "logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        atexit.register(self.shutdown)

    def ensure_started(self) -> None:
        if self.playwright_enabled:
            self._ensure_playwright()

    def health(self) -> dict[str, Any]:
        playwright_listening = _is_port_open("localhost", self.playwright_port)
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
        process = self._playwright_process
        if process is not None and process.poll() is None:
            if os.name == "nt":
                # terminate() ends cmd.exe only and leaves the node server
                # running; /T takes the whole tree.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.terminate()
        for handle in self._log_handles:
            try:
                handle.close()
            except OSError:
                pass
        self._log_handles = []

    def _ensure_playwright(self) -> None:
        if _is_port_open("localhost", self.playwright_port):
            return

        stdout_path = self._logs_dir / "playwright.out.log"
        stderr_path = self._logs_dir / "playwright.err.log"

        stdout_handle = open(stdout_path, "a", encoding="utf-8")
        stderr_handle = open(stderr_path, "a", encoding="utf-8")
        self._log_handles = [stdout_handle, stderr_handle]

        self._playwright_process = subprocess.Popen(
            [
                "cmd",
                "/c",
                "npx",
                f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}",
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
