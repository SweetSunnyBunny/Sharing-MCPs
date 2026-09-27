"""Authenticated machine-connector entry point; never a public MCP backdoor."""
import hmac
import asyncio
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from services import anam_tool_gateway as gateway

router = APIRouter(prefix="/api/tool-gateway", tags=["tool-gateway"])


class GatewayRequest(BaseModel):
    include_schema: bool = True
    result_limit: int = Field(default=8000, ge=1, le=16000)
    operation: str
    query: str = Field(default="", max_length=500)
    server: str = Field(default="", max_length=100)
    tool: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=15, ge=1, le=50)
    arguments: dict = Field(default_factory=dict)
    identity: str = Field(default="", max_length=50)
    conversation_id: str = Field(default="", max_length=100)
    request_id: str = Field(default="", max_length=100)
    job_id: str = Field(default="", max_length=100)


@router.post("")
async def tool_gateway(request: Request):
    # Require machine credentials even when normal web login is disabled or a
    # browser has an Anam cookie. Cross-origin pages cannot drive these tools.
    key = os.getenv("ANAM_API_KEY", "")
    if not key or not hmac.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {key}".encode()):
        raise HTTPException(401, "Machine authentication required")
    size = 0
    parts = []
    async for part in request.stream():
        size += len(part)
        if size > 1_000_000:
            raise HTTPException(413, "Gateway request is too large")
        parts.append(part)
    try:
        payload = GatewayRequest.model_validate_json(b"".join(parts))
    except (ValidationError, ValueError):
        raise HTTPException(400, "Invalid gateway request") from None
    try:
        if payload.operation == "discover":
            return await gateway.discover(
                payload.query, payload.server, payload.offset, payload.limit,
                payload.include_schema, payload.identity, payload.conversation_id,
            )
        if payload.operation == "invoke":
            return await gateway.invoke(payload.server, payload.tool, payload.arguments, payload.identity,
                                        payload.conversation_id, payload.request_id)
        if payload.operation == "job":
            return await gateway.job_result(payload.job_id, payload.identity, payload.conversation_id)
        if payload.operation == "result":
            return await gateway.result_page(payload.job_id, payload.offset, payload.result_limit, payload.query, payload.identity, payload.conversation_id)
        raise HTTPException(400, "Unknown gateway operation")
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(502, "Tool discovery failed; check the server's connection in Anam") from None


class ApprovalDecision(BaseModel):
    decision: str
    identity: str
    conversation_id: str
    content: dict | None = None


@router.get('/approvals')
async def pending_approvals(identity: str = '', conversation_id: str = ''):
    from services.codex_approvals import pending
    return {'requests': pending(identity, conversation_id)}


@router.post('/approvals/{approval_id}')
async def decide_approval(approval_id: str, payload: ApprovalDecision):
    from services.codex_approvals import resolve
    try:
        return resolve(approval_id, payload.decision, payload.identity, payload.conversation_id, payload.content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/runtime')
async def runtime_report():
    from services import codex_sessions, runtime_metrics
    from services.context_ledger import get_context_ledgers
    from services.resource_history import report as resource_report
    history = await asyncio.to_thread(resource_report)
    return {'codex_processes':codex_sessions.status(), 'timings':runtime_metrics.report(),
            'context':get_context_ledgers(), 'resources':history}


@router.get('/resources')
async def resource_history(limit: int = 120):
    from services.resource_history import report
    return await asyncio.to_thread(report,limit)
