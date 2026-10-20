"""Watchdog services come only from explicit local configuration."""
import json
from services import stack_watchdog

def test_no_configuration_does_not_watch_or_restart_services(monkeypatch):
    monkeypatch.delenv("ANAM_WATCHDOG_CONFIG", raising=False)
    assert stack_watchdog._build_services() == []

def test_reads_explicit_service_configuration(monkeypatch, tmp_path):
    config = tmp_path / "watchdog.json"
    config.write_text(json.dumps([{"name":"example", "args":["python", "supervisor.py", "restart"], "ports":[8811], "via_supervisor":True}]))
    monkeypatch.setenv("ANAM_WATCHDOG_CONFIG", str(config))
    services = stack_watchdog._build_services()
    assert len(services) == 1
    assert services[0].ports == (8811,)
    assert services[0].via_supervisor is True

def test_rejects_invalid_service_name(monkeypatch, tmp_path):
    config = tmp_path / "watchdog.json"
    config.write_text(json.dumps([{"name":"../outside", "args":["python"], "ports":[8811]}]))
    monkeypatch.setenv("ANAM_WATCHDOG_CONFIG", str(config))
    assert stack_watchdog._build_services() == []
