"""Aisling's public doorway must have supervised local services behind it."""
import importlib.util
from pathlib import Path
import sys


STACK = Path(__file__).resolve().parents[2] / "scripts" / "anam_stack.py"
spec = importlib.util.spec_from_file_location("aisling_stack_test", STACK)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_aisling_services_are_owned_by_the_Home_supervisor():
    services = module._service_specs()
    assert services["aisling-api"].ports == (8000,)
    assert services["aisling-api"].health_url.endswith("/health")
    assert services["aisling-web"].ports == (8081,)
    assert services["aisling-web"].cwd.name == "frontend"
    assert "aisling-api" in module.START_ORDER
    assert "aisling-web" in module.START_ORDER
    assert 8000 in module.MANAGED_PORTS and 8081 in module.MANAGED_PORTS
