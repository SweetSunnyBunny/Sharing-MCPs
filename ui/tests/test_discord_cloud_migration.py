"""Cloud Discord must not leave a local service/watchdog/readiness dependency."""
import ast
import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit

from services import stack_watchdog


def test_local_watchdog_cannot_resurrect_discord():
    watched = stack_watchdog._build_services()
    assert all(service.name != 'discord-mcp' for service in watched)
    assert all(8768 not in service.ports for service in watched)


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


def test_startup_checks_cloud_health_without_exposing_mcp_secret():
    path = Path(__file__).resolve().parents[1] / 'core/lifespan.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == '_MCP_READINESS_URLS' for t in node.targets))
    urls = dict(ast.literal_eval(assignment.value))
    parsed = urlsplit(urls['discord-backend'])
    assert parsed.scheme == 'https'
    assert parsed.hostname == 'discord-backend.YOUR-BACKEND.YOUR-ACCOUNT.workers.dev'
    assert parsed.path == '/health'
    assert not parsed.query
