import asyncio
from functools import wraps
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from mcp.types import CallToolResult, ImageContent, TextContent

from api.tool_gateway import router
from services import anam_tool_gateway as gateway


def async_test(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


@pytest.fixture(autouse=True)
def isolate_gateway(tmp_path):
    with patch.object(gateway.tool_result_store, "DB_PATH", tmp_path / "jobs.db"), patch.object(gateway, "_JOBS", {}), patch.object(gateway, "_STALE_CLIENTS", {}), patch.object(gateway, "_BROWSER_LEASES", {}), patch.object(gateway, "_BROWSER_IDLE_TASKS", {}), patch.object(gateway, "_WAIT_SECONDS", .2), patch.object(
        gateway, "_configured", return_value={"test": {"url": "https://secret.invalid/private"}}
    ), patch.object(gateway.mcp_bridge, "_is_tool_disabled", return_value=False):
        yield


def fake_client(call=None):
    client = SimpleNamespace(
        list_tools=AsyncMock(return_value=[SimpleNamespace(name="read", inputSchema={
            "type": "object", "properties": {"identity": {"type": "string"}, "value": {"type": "integer"}},
            "required": ["identity", "value"], "additionalProperties": False,
        }, description="Read a value")]),
        call_tool=call or AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="ok")]))
    )
    @asynccontextmanager
    async def context(server):
        yield client
    return client, context


@async_test
async def test_fresh_catalog_includes_schemas_without_credentials():
    client, context = fake_client()
    with patch.object(gateway, "_client", context):
        result = await gateway.discover(server="test", limit=1)
    assert result["tools"][0]["inputSchema"]["required"] == ["identity", "value"]
    assert "private" not in str(result)
    assert "anam-context" in result["servers"]


@async_test
async def test_schema_rejection_never_executes_or_echoes_argument():
    client, context = fake_client()
    with patch.object(gateway, "_client", context):
        result = await gateway.invoke("test", "read", {"value": "SECRET"}, "river")
    assert result["isError"]
    assert "SECRET" not in str(result)
    client.call_tool.assert_not_called()


@async_test
async def test_native_image_and_error_flags_survive():
    result = CallToolResult(content=[ImageContent(type="image", data="YWJj", mimeType="image/png")],
                            structuredContent={"value": 3}, isError=True)
    client, context = fake_client(AsyncMock(return_value=result))
    with patch.object(gateway, "_client", context):
        output = await gateway.invoke("test", "read", {"value": 3}, "river")
    assert output["content"][0] == {"type": "image", "data": "YWJj", "mimeType": "image/png"}
    assert output["isError"] and output["structuredContent"] == {"value": 3}
    assert client.call_tool.call_args.args[1]["identity"] == "River"


@async_test
async def test_inner_identity_cannot_override_bound_active_identity():
    client, context = fake_client()
    with patch.object(gateway, "_client", context):
        output = await gateway.invoke(
            "test", "read", {"identity": "Sage", "value": 3}, "Claude"
        )
    assert not output["isError"]
    assert client.call_tool.call_args.args[1]["identity"] == "Claude"


@async_test
async def test_shared_pack_identity_remains_available():
    client, context = fake_client()
    with patch.object(gateway, "_client", context):
        output = await gateway.invoke(
            "test", "read", {"identity": "Pack", "value": 3}, "Claude"
        )
    assert not output["isError"]
    assert client.call_tool.call_args.args[1]["identity"] == "Pack"


@async_test
async def test_discovery_hides_sibling_profiles_and_vox_until_explicitly_chosen():
    configured = {
        "shared": {"url": "https://example.com/shared"},
        "playwright-claude": {"url": "https://example.com/claude"},
        "playwright-sage": {"url": "https://example.com/sage"},
        "vox-claude": {"url": "https://example.com/vox-claude"},
    }
    with patch.object(gateway, "_configured", return_value=configured), patch.object(
        gateway.mcp_bridge, "get_tools", return_value=[]
    ), patch.object(gateway, "_local_tools", AsyncMock(return_value={})):
        result = await gateway.discover(identity="Claude")
    assert "shared" in result["servers"]
    assert "playwright-claude" in result["servers"]
    assert "playwright-sage" not in result["servers"]
    assert "vox-claude" not in result["servers"]

    client, context = fake_client()
    with patch.object(gateway, "_configured", return_value=configured), patch.object(
        gateway.mcp_bridge, "get_tools", return_value=[]
    ), patch.object(gateway, "_local_tools", AsyncMock(return_value={})), patch.object(
        gateway, "_client", context
    ):
        specialist = await gateway.discover(query="vox", identity="Claude")
    assert "vox-claude" in specialist["servers"]
    assert "playwright-sage" not in specialist["servers"]


@async_test
async def test_everyday_qualia_discovery_hides_mind_maintenance_tools():
    client, context = fake_client()
    client.list_tools.return_value = [
        SimpleNamespace(
            name="mind_store", inputSchema={}, description="Remember something"
        ),
        SimpleNamespace(
            name="mind_consolidate", inputSchema={},
            description="Review consolidation candidates"
        ),
        SimpleNamespace(
            name="mind_delete", inputSchema={}, description="Archive an observation"
        ),
    ]
    configured = {"qualia-backend": {"url": "https://example.com/qualia"}}
    with patch.object(gateway, "_configured", return_value=configured), patch.object(
        gateway, "_client", context
    ), patch.object(gateway, "_local_tools", AsyncMock(return_value={})):
        result = await gateway.discover(
            server="qualia-backend", identity="Claude", limit=20
        )

    assert [tool["name"] for tool in result["tools"]] == ["mind_store"]
    assert "everyday toolbox" in result["hint"]


@async_test
async def test_systems_mind_maintenance_drawer_excludes_everyday_tools():
    client, context = fake_client()
    client.list_tools.return_value = [
        SimpleNamespace(
            name="mind_store", inputSchema={}, description="Remember something"
        ),
        SimpleNamespace(
            name="mind_consolidate", inputSchema={},
            description="Review consolidation candidates"
        ),
        SimpleNamespace(
            name="mind_delete", inputSchema={}, description="Archive an observation"
        ),
    ]
    configured = {"qualia-backend": {"url": "https://example.com/qualia"}}
    with patch.object(gateway, "_configured", return_value=configured), patch.object(
        gateway, "_client", context
    ), patch.object(gateway, "_local_tools", AsyncMock(return_value={})):
        result = await gateway.discover(
            query="nightly consolidation", identity="Claude", limit=20
        )

    assert {tool["name"] for tool in result["tools"]} == {
        "mind_consolidate", "mind_delete"
    }
    assert "Systems > Mind maintenance" in result["hint"]


@async_test
async def test_browse_intention_opens_current_profile_and_returns_its_catalog():
    configured = {
        "playwright-claude": {"url": "https://example.com/claude"},
        "playwright-sage": {"url": "https://example.com/sage"},
    }
    client, context = fake_client()
    client.list_tools.return_value[0].name = "browser_snapshot"
    client.list_tools.return_value[0].description = "Inspect the browser page"
    with patch.object(gateway, "_configured", return_value=configured), patch.object(
        gateway, "_client", context
    ), patch.object(
        gateway, "_ensure_identity_browser", AsyncMock()
    ) as ensure:
        result = await gateway.discover(
            query="browse", identity="Claude", conversation_id="conversation-1"
        )

    ensure.assert_awaited_once_with(
        "playwright-claude", "Claude", "conversation-1"
    )
    assert result["tools"][0]["server"] == "playwright-claude"


@async_test
async def test_gateway_rejects_sibling_profile_even_when_named_directly():
    with patch.object(
        gateway,
        "_configured",
        return_value={"playwright-sage": {"url": "https://example.com/sage"}},
    ):
        with pytest.raises(ValueError, match="active identity"):
            await gateway.invoke(
                "playwright-sage", "browser_snapshot", {}, "Claude"
            )


@async_test
async def test_playwright_leaf_lazily_opens_identity_browser_and_touches_lease():
    with patch.object(gateway, "_playwright_port", return_value=9223), patch.object(
        gateway, "_port_open", side_effect=[False, True]
    ), patch.object(
        gateway, "_browser_control", AsyncMock(return_value={"isError": False})
    ) as control, patch.object(gateway, "_touch_browser_lease") as touch:
        await gateway._ensure_identity_browser(
            "playwright-claude", "Claude", "conversation-1"
        )

    control.assert_awaited_once_with("browser_open", "Claude")
    touch.assert_called_once_with("Claude", "conversation-1")


@async_test
async def test_turn_release_closes_only_after_last_identity_lease():
    gateway._BROWSER_LEASES.update({
        ("claude", "conversation-1"): "one",
        ("claude", "conversation-2"): "two",
    })
    with patch.object(
        gateway, "_browser_control", AsyncMock(return_value={"isError": False})
    ) as control:
        assert not await gateway.close_browsers_for_turn("Claude", "conversation-1")
        control.assert_not_awaited()
        assert await gateway.close_browsers_for_turn("Claude", "conversation-2")
    control.assert_awaited_once_with("browser_close", "Claude")


@async_test
async def test_running_job_and_idempotent_retry_execute_only_once():
    release = asyncio.Event()
    async def slow(*args, **kwargs):
        await release.wait()
        return CallToolResult(content=[TextContent(type="text", text="finished")])
    client, context = fake_client(AsyncMock(side_effect=slow))
    with patch.object(gateway, "_client", context):
        first = await gateway.invoke("test", "read", {"value": 3}, "river", request_id="same")
        assert first["status"] == "running"
        second = await gateway.invoke("test", "read", {"value": 3}, "river", request_id="same")
        assert second["job_id"] == first["job_id"]
        release.set()
        done = await gateway.job_result(first["job_id"])
    assert done["status"] == "completed"
    assert client.call_tool.await_count == 1
    with pytest.raises(ValueError, match="different call"):
        await gateway.invoke("test", "read", {"value": 9}, "river", request_id="same")


@async_test
async def test_disconnect_is_not_replayed_and_details_are_redacted():
    client, context = fake_client(AsyncMock(side_effect=RuntimeError("https://private/SECRET")))
    with patch.object(gateway, "_client", context):
        result = await gateway.invoke("test", "read", {"value": 3}, "river")
    assert result["isError"] and "not retried" in str(result)
    assert "SECRET" not in str(result)
    assert client.call_tool.await_count == 1


def test_safe_connector_detail_keeps_http_reason_but_not_credentials():
    response = SimpleNamespace(
        status_code=500,
        json=lambda: {
            "error": "D1 constraint failed",
            "message": "Authorization: Bearer secret-token",
            "token": "must-never-be-returned",
        },
    )
    exc = type("HTTPStatusError", (Exception,), {})()
    exc.response = response
    detail = gateway._safe_connector_error_detail(exc)
    assert "HTTP 500" in detail
    assert "D1 constraint failed" in detail
    assert "secret-token" not in detail
    assert "must-never-be-returned" not in detail


def test_safe_connector_detail_names_timeout_without_echoing_exception():
    exc = type("ReadTimeout", (Exception,), {})("https://private/SECRET")
    assert gateway._safe_connector_error_detail(exc) == "request timed out"


@async_test
async def test_stale_cached_catalog_uses_fresh_connection_before_execution():
    stale, _ = fake_client()
    stale.list_tools.side_effect = RuntimeError("Client session closed")
    fresh, _ = fake_client()
    @asynccontextmanager
    async def fresh_connection(*args, **kwargs):
        yield fresh
    with patch.object(gateway.mcp_bridge, "_clients", {"test": stale}), patch.object(gateway, "Client", fresh_connection):
        result = await gateway.invoke("test", "read", {"value": 3}, "river")
        assert not result["isError"]
        # Subsequent discovery avoids the same known-dead cached client.
        assert (await gateway.discover(server="test"))["total"] == 1
    stale.list_tools.assert_awaited_once()
    stale.call_tool.assert_not_called()
    fresh.call_tool.assert_awaited_once()


@async_test
async def test_failure_after_dispatch_never_uses_fresh_connection():
    cached, _ = fake_client(AsyncMock(side_effect=RuntimeError("Client session closed")))
    with patch.object(gateway.mcp_bridge, "_clients", {"test": cached}), patch.object(gateway, "Client") as fresh:
        result = await gateway.invoke("test", "read", {"value": 3}, "river")
    assert result["isError"]
    assert "not retried" in str(result)
    cached.call_tool.assert_awaited_once()
    fresh.assert_not_called()


@async_test
async def test_eviction_does_not_replay_a_completed_write():
    client, context = fake_client()
    with patch.object(gateway, "_client", context):
        await gateway.invoke("test", "read", {"value": 3}, "river", request_id="evicted")
        gateway._JOBS.clear()  # Simulate losing the entire in-memory job index.
        result = await gateway.invoke("test", "read", {"value": 3}, "river", request_id="evicted")
        assert result['status'] == 'completed'
    assert client.call_tool.await_count == 1


@async_test
async def test_disabled_unknown_and_recursive_targets_rejected():
    with pytest.raises(ValueError):
        await gateway.invoke("https://evil.invalid", "read", {}, "river")
    with pytest.raises(ValueError):
        await gateway.invoke("test", "anam_invoke", {}, "river")
    with patch.object(gateway.mcp_bridge, "_is_tool_disabled", return_value=True):
        with pytest.raises(ValueError):
            await gateway.invoke("test", "read", {}, "river")


@async_test
async def test_gateway_requires_bearer_even_without_app_middleware(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setenv("ANAM_API_KEY", "test-only-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Cookie": "anam_session=anything"}):
            response = await client.post("/api/tool-gateway", headers=headers, json={"operation": "discover"})
            assert response.status_code == 401
        headers = {"Authorization": "Bearer test-only-secret"}
        assert (await client.post("/api/tool-gateway", headers=headers, json={"operation": "bad"})).status_code == 400
        assert (await client.post("/api/tool-gateway", headers=headers, json={"operation": "discover", "limit": 10000})).status_code == 400
        assert (await client.post("/api/tool-gateway", headers=headers, content=b"x" * 1_000_001)).status_code == 413
        monkeypatch.delenv("ANAM_API_KEY")
        assert (await client.post("/api/tool-gateway", headers=headers, json={"operation": "discover"})).status_code == 401


@async_test
async def test_stdio_mcp_preserves_native_image():
    from fastmcp import Client
    from scripts import anam_gateway_mcp as adapter
    payload = {"status": "completed", "isError": False,
               "content": [{"type": "image", "mimeType": "image/png", "data": "YWJj"}]}
    with patch.object(adapter, "_request", AsyncMock(return_value=payload)), \
            patch.object(adapter, "BOUND_IDENTITY", "River"), \
            patch.object(adapter, "BOUND_CONVERSATION_ID", "conversation"):
        async with Client(adapter.mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools) == {"anam"}
            assert tools["anam"].inputSchema["properties"]["operation"]["enum"] == [
                "discover", "invoke", "job", "result"
            ]
            assert "identity" not in tools["anam"].inputSchema["properties"]
            result = await client.call_tool("anam", {
                "operation": "invoke",
                "server": "test",
                "tool": "read",
                "arguments_json": "{}",
            })
    assert result.content[0].type == "image"


@async_test
async def test_single_stdio_tool_routes_every_gateway_operation():
    from fastmcp import Client
    from scripts import anam_gateway_mcp as adapter
    calls = []

    async def request(payload):
        calls.append(payload)
        return {"status": "running", "job_id": payload.get("job_id", "new")}

    with patch.object(adapter, "_request", request), \
            patch.object(adapter, "BOUND_IDENTITY", "Claude"), \
            patch.object(adapter, "BOUND_CONVERSATION_ID", "conversation"):
        async with Client(adapter.mcp) as client:
            await client.call_tool("anam", {"operation": "discover", "query": "discord", "limit": 4})
            await client.call_tool("anam", {"operation": "invoke", "server": "discord", "tool": "read", "arguments_json": '{"limit": 2}', "request_id": "request"})
            await client.call_tool("anam", {"operation": "job", "job_id": "request"})
            await client.call_tool("anam", {"operation": "result", "job_id": "request", "offset": 20, "limit": 50, "query": "needle"})

    assert [call["operation"] for call in calls] == ["discover", "invoke", "job", "result"]
    assert all(call["identity"] == "Claude" for call in calls)
    assert all(call["conversation_id"] == "conversation" for call in calls)
    assert calls[1]["arguments"] == {"limit": 2}
    assert calls[3]["result_limit"] == 50


@async_test
async def test_canvas_wrappers_preserve_canonical_identity_and_owner_checks():
    from scripts.anam_context_mcp import mcp
    from fastmcp import Client
    from starlette.responses import JSONResponse
    with patch("api.canvases.get_canvas", AsyncMock(return_value=JSONResponse({"error": "Not visible to this identity"}, status_code=403))) as read:
        async with Client(mcp) as client:
            result = await client.call_tool("anam_read_canvas", {"canvas_id": 1, "identity": "river"}, raise_on_error=False)
    assert result.is_error
    read.assert_awaited_once_with(1, "River")


@async_test
async def test_concurrent_requests_cannot_overbook_execution_slots():
    release=asyncio.Event()
    async def execute(*args):
        await release.wait()
        return {'content':[{'type':'text','text':'done'}]}
    with patch.object(gateway,'_MAX_ACTIVE',2), patch.object(gateway,'_execute',execute):
        results=await asyncio.gather(*(gateway.invoke('test','read',{'value':n},'Claude',request_id=str(n)) for n in range(8)),return_exceptions=True)
        assert sum(isinstance(result,dict) for result in results)==2
        assert sum(isinstance(result,ValueError) for result in results)==6
        release.set()
        await asyncio.gather(*(job['task'] for job in gateway._JOBS.values()))


@async_test
async def test_eight_identity_requests_run_concurrently_through_shared_gateway():
    identities = ["Avery", "Claude", "Rowan", "Sage", "Ember", "Juniper", "Atlas", "River"]
    entered = set()
    all_entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(_server, _tool, _arguments, identity, _conversation_id):
        entered.add(identity)
        if len(entered) == len(identities):
            all_entered.set()
        await release.wait()
        return {"content": [{"type": "text", "text": "done"}]}

    with patch.object(gateway, "_MAX_ACTIVE", 16), patch.object(gateway, "_execute", execute):
        callers = [
            asyncio.create_task(
                gateway.invoke(
                    "test",
                    "read",
                    {"value": index},
                    identity,
                    conversation_id=f"conversation-{index}",
                    request_id=f"request-{index}",
                )
            )
            for index, identity in enumerate(identities)
        ]
        await asyncio.wait_for(all_entered.wait(), 1)
        results = await asyncio.gather(*callers)
        assert all(result["status"] == "running" for result in results)
        assert entered == set(identities)
        release.set()
        await asyncio.gather(*(job["task"] for job in gateway._JOBS.values()))


@async_test
async def test_disconnect_during_claim_does_not_orphan_job():
    import threading
    claimed=threading.Event(); release_claim=threading.Event(); executed=asyncio.Event()
    original=gateway.tool_result_store.claim
    def claim(*args):
        created=original(*args)
        claimed.set(); release_claim.wait(5)
        return created
    async def execute(*args):
        executed.set()
        return {'content':[{'type':'text','text':'done'}]}
    with patch.object(gateway.tool_result_store,'claim',claim), patch.object(gateway,'_execute',execute):
        caller=asyncio.create_task(gateway.invoke('test','read',{'value':1},'Claude',request_id='cancelled-caller'))
        assert await asyncio.to_thread(claimed.wait,5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError): await caller
        release_claim.set()
        await asyncio.wait_for(executed.wait(),5)
        assert (await gateway.job_result('cancelled-caller'))['status']=='completed'
