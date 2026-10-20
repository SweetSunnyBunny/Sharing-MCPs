from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = "http://127.0.0.1:8811"
DEFAULT_TIMEOUT = 120


class MachineAgentError(RuntimeError):
    pass


@dataclass(slots=True)
class MachineAgentClient:
    base_url: str
    api_key: str
    timeout: int

    @classmethod
    def from_env(cls) -> "MachineAgentClient":
        api_key = os.environ.get("MACHINE_AGENT_API_KEY", "").strip()
        key_file = os.environ.get("MACHINE_AGENT_KEY_FILE", "").strip()
        if not api_key and key_file:
            api_key = Path(key_file).expanduser().read_text(encoding="utf-8").strip()
        return cls(
            base_url=os.environ.get("MACHINE_AGENT_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            api_key=api_key,
            timeout=int(os.environ.get("MACHINE_AGENT_TIMEOUT", DEFAULT_TIMEOUT)),
        )

    def invoke(self, tool: str, arguments: dict[str, Any]) -> Any:
        if not self.api_key:
            raise MachineAgentError("Set MACHINE_AGENT_API_KEY or MACHINE_AGENT_KEY_FILE before invoking the bridge")
        payload = json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = request.Request(
            f"{self.base_url}/tools/invoke",
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            raise MachineAgentError(
                f"Machine agent returned HTTP {exc.code}: {message}"
            ) from exc
        except error.URLError as exc:
            raise MachineAgentError(
                f"Machine agent is unreachable at {self.base_url}: {exc.reason}"
            ) from exc

        if not body.strip():
            return ""

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return body

        if isinstance(data, dict) and "ok" in data:
            if data.get("ok"):
                return data.get("result")
            raise MachineAgentError(str(data.get("error", "Unknown machine-agent error")))

        return data


_client = MachineAgentClient.from_env()


async def invoke_tool(tool: str, **arguments: Any) -> Any:
    return await asyncio.to_thread(_client.invoke, tool, arguments)
