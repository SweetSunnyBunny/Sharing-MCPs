"""The stack supervisor must survive long enough to attest to Anam restarts."""

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT.parent / "scripts" / "anam_stack.py"
HELPER = ROOT / "scripts" / "restart_anam_when_idle.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_supervisor_writes_verified_receipt_after_pid_change(tmp_path, monkeypatch):
    stack = _load("anam_stack_receipt_test", STACK)
    monkeypatch.setattr(stack, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(stack, "REQUEST_PATH", tmp_path / "request.json")
    stack.REQUEST_PATH.write_text(json.dumps({
        "action": "restart",
        "service": "anam",
        "reason": "gateway activation",
        "receipt_name": stack.RESTART_RECEIPT_NAME,
    }), encoding="utf-8")

    supervisor = object.__new__(stack.StackSupervisor)
    supervisor.manifest = {"services": {"anam": {"pid": 101}}}

    def restart(name, reason):
        assert name == "anam"
        assert reason == "gateway activation"
        supervisor.manifest["services"][name]["pid"] = 202
        return True

    supervisor.restart_one = restart
    supervisor.process_request()

    receipt = json.loads((tmp_path / stack.RESTART_RECEIPT_NAME).read_text(encoding="utf-8"))
    assert receipt["status"] == "verified"
    assert receipt["old_pid"] == 101
    assert receipt["new_pid"] == 202
    assert receipt["health"] == "ready"


def test_supervisor_marks_receipt_failed_without_a_pid_change(tmp_path, monkeypatch):
    stack = _load("anam_stack_failed_receipt_test", STACK)
    monkeypatch.setattr(stack, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(stack, "REQUEST_PATH", tmp_path / "request.json")
    stack.REQUEST_PATH.write_text(json.dumps({
        "action": "restart",
        "service": "anam",
        "receipt_name": stack.RESTART_RECEIPT_NAME,
    }), encoding="utf-8")

    supervisor = object.__new__(stack.StackSupervisor)
    supervisor.manifest = {"services": {"anam": {"pid": 101}}}
    supervisor.restart_one = lambda name, reason: False
    supervisor.process_request()

    receipt = json.loads((tmp_path / stack.RESTART_RECEIPT_NAME).read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["old_pid"] == receipt["new_pid"] == 101
    assert receipt["health"] == "unverified"


def test_idle_helper_queues_a_supervisor_owned_receipt(tmp_path, monkeypatch):
    helper = _load("anam_restart_helper_test", HELPER)
    monkeypatch.setattr(helper, "REQUEST", tmp_path / "request.json")
    monkeypatch.setattr(helper, "RECEIPT", tmp_path / "anam-armed-restart.json")

    helper.queue_supervisor_restart("single gateway")

    request = json.loads(helper.REQUEST.read_text(encoding="utf-8"))
    assert request["action"] == "restart"
    assert request["service"] == "anam"
    assert request["reason"] == "single gateway"
    assert request["receipt_name"] == "anam-armed-restart.json"


def test_autowake_dm_nudge_has_a_live_history_fallback():
    from services.autowake import DISCORD_NUDGE

    assert "discord_check_dm_notifications when it is exposed" in DISCORD_NUDGE
    assert "discord_read_dm_messages" in DISCORD_NUDGE
