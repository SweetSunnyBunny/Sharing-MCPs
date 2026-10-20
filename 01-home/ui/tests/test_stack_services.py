"""Check current house ownership without resurrecting retired Aisling services."""
import importlib.util
from pathlib import Path
import sys

import pytest


STACK = Path(__file__).resolve().parents[2] / "scripts" / "anam_stack.py"


@pytest.fixture
def stack():
    if not STACK.is_file():
        pytest.skip("The optional Home house supervisor is outside this checkout")
    spec = importlib.util.spec_from_file_location("stack_services_test", STACK)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def test_anam_and_pages_have_supervised_health_checks(stack):
    services = stack._service_specs()
    assert services["anam"].ports == (8790,)
    assert services["anam"].health_url == "http://127.0.0.1:8790/health"
    assert services["pages"].ports == (8100, 8101, 8102, 8103, 8791)
    assert services["pages"].health_url == "http://127.0.0.1:8791/api/health"
    assert services["tools"].ports == (8815,)
    assert services["tools"].health_url == "http://127.0.0.1:8815/health"
    assert "anam" in stack.START_ORDER
    assert "pages" in stack.START_ORDER
    # The shared tool host carries the Anam gateway, so it must be up first.
    assert stack.START_ORDER.index("tools") < stack.START_ORDER.index("anam")


def test_retired_aisling_services_do_not_reserve_ports(stack):
    services = stack._service_specs()
    for name in ("aisling-api", "aisling-web"):
        assert name not in services
        assert name not in stack.START_ORDER
    assert {8000, 8081}.isdisjoint(stack.MANAGED_PORTS)
