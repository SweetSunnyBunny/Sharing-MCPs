"""Private computer operations and redacted health for the existing Anam gateway."""
import asyncio
import hmac
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from services import connector_health, interactive_terminal

router = APIRouter(prefix="/api/computer", tags=["computer"])


class TerminalRequest(BaseModel):
    operation: str
    session_id: str = Field(default="", max_length=64)
    command: str = Field(default="", max_length=32000)
    cwd: str = Field(default="", max_length=1024)
    shell: str = "powershell"
    cursor: int = Field(default=0, ge=0)
    max_chars: int = Field(default=24000, ge=1, le=50000)
    wait_ms: int = Field(default=0, ge=0, le=10000)
    text: str = Field(default="", max_length=16000)
    enter: bool = False


@router.post("/terminal")
async def terminal(request: Request):
    key = os.getenv("ANAM_API_KEY", "")
    if not key or not hmac.compare_digest(request.headers.get("authorization", "").encode(), ("Bearer " + key).encode()):
        raise HTTPException(401, "Machine authentication required")
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > 200_000:
            raise HTTPException(413, "Terminal request too large")
    try:
        data = TerminalRequest.model_validate_json(body)
    except ValidationError:
        raise HTTPException(400, "Invalid terminal request") from None
    calls = {
        "start": (interactive_terminal.start, (data.command, data.cwd, data.shell)),
        "read": (interactive_terminal.read, (data.session_id, data.cursor, data.max_chars, data.wait_ms)),
        "write": (interactive_terminal.write, (data.session_id, data.text, data.enter)),
        "interrupt": (interactive_terminal.interrupt, (data.session_id,)),
        "close": (interactive_terminal.close, (data.session_id,)),
    }
    if data.operation not in calls:
        raise HTTPException(400, "Unknown terminal operation")
    fn, args = calls[data.operation]
    try:
        return await asyncio.to_thread(fn, *args)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, "Terminal operation failed (" + type(exc).__name__ + "); inspect the session before repeating a command") from None


@router.get("/connectors")
async def connectors(server: str = "", probe: bool = False):
    from services.anam_tool_gateway import discover, _configured
    if probe:
        if not server or server not in set(_configured()) | {"anam-context"}:
            raise HTTPException(400, "Choose one configured server to check")
        try:
            await asyncio.wait_for(discover(server=server, limit=1), timeout=30)
            connector_health.observe(server)
        except Exception as exc:
            connector_health.observe(server, error=exc)
    result = connector_health.snapshot()
    if server:
        result["servers"] = [r for r in result["servers"] if r["server"] == server]
    return result
