"""Cloud Discord must not leave a local service/watchdog/readiness dependency."""
import ast
import pytest
import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit

from services import stack_watchdog


def test_local_watchdog_cannot_resurrect_discord():
    watched = stack_watchdog._build_services()
    assert all(service.name != 'discord-mcp' for service in watched)
    assert all(8768 not in service.ports for service in watched)


@pytest.mark.skipif(not (Path(__file__).resolve().parents[2] / "scripts/anam_stack.py").exists(), reason="Optional machine-specific supervisor is not bundled")
def test_start_stop_supervisor_does_not_own_discord():
    path = Path(__file__).resolve().parents[2] / 'scripts/anam_stack.py'
    spec = importlib.util.spec_from_file_location('discord_migration_stack_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        assert 'discord-mcp' not in module._service_specs()
        assert 'discord-mcp' not in module.START_ORDER
        assert 'discord-mcp' not in module.STOP_ORDER
        assert 8768 not in module.MANAGED_PORTS
        assert 8768 not in module.PORT_OWNER_RULES
    finally:
        sys.modules.pop(spec.name, None)


def test_startup_checks_cloud_health_without_exposing_mcp_secret(monkeypatch):
    from core.lifespan import _mcp_readiness_urls
    monkeypatch.delenv("ANAM_SKIP_MCP_READINESS", raising=False)
    monkeypatch.delenv("ANAM_DISCORD_HEALTH_URL", raising=False)
    assert _mcp_readiness_urls() == []
    monkeypatch.setenv("ANAM_DISCORD_HEALTH_URL", "https://discord.installation.invalid/health")
    urls = dict(_mcp_readiness_urls())
    parsed = urlsplit(urls["discord-backend"])
    assert parsed.scheme == "https" and parsed.path == "/health" and not parsed.query
