import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services import integration_owner


class IntegrationOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.tmpdir.name) / "integration.lock"
        integration_owner._LOCK_FD = None
        integration_owner._LOCK_METADATA = None

    def tearDown(self):
        integration_owner.release_integration_ownership()
        integration_owner._LOCK_FD = None
        integration_owner._LOCK_METADATA = None
        self.tmpdir.cleanup()

    def test_second_instance_enters_standby_when_lock_exists(self):
        with patch.object(integration_owner, "EXCLUSIVE_INTEGRATIONS", True), patch.object(
            integration_owner, "_LOCK_PATH", self.lock_path
        ):
            first = integration_owner.claim_integration_ownership()
            self.assertTrue(first["owner"])

            held_fd = integration_owner._LOCK_FD
            held_meta = dict(integration_owner._LOCK_METADATA or {})
            integration_owner._LOCK_FD = None
            integration_owner._LOCK_METADATA = None

            second = integration_owner.claim_integration_ownership()
            self.assertFalse(second["owner"])
            self.assertEqual(second["reason"], "owned_by_another_instance")
            self.assertEqual(second["metadata"].get("pid"), held_meta.get("pid"))

            integration_owner._LOCK_FD = held_fd
            integration_owner._LOCK_METADATA = held_meta

    def test_stale_lock_is_reclaimed(self):
        stale = {
            "pid": 999999,
            "instance_id": "stale123",
        }
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text('{"pid": 999999, "instance_id": "stale123"}', encoding="utf-8")

        with patch.object(integration_owner, "EXCLUSIVE_INTEGRATIONS", True), patch.object(
            integration_owner, "_LOCK_PATH", self.lock_path
        ), patch.object(integration_owner, "_pid_is_running", return_value=False):
            claimed = integration_owner.claim_integration_ownership()

        self.assertTrue(claimed["owner"])
        self.assertEqual(claimed["reason"], "reclaimed_stale_lock")
        self.assertNotEqual(claimed["metadata"].get("instance_id"), stale["instance_id"])

    def test_existing_anam_owner_is_taken_over_when_enabled(self):
        stale = {
            "pid": 12345,
            "instance_id": "older-anam",
        }
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text('{"pid": 12345, "instance_id": "older-anam"}', encoding="utf-8")

        with patch.object(integration_owner, "EXCLUSIVE_INTEGRATIONS", True), patch.object(
            integration_owner, "TAKEOVER_DUPLICATE_INTEGRATIONS", True
        ), patch.object(
            integration_owner, "_LOCK_PATH", self.lock_path
        ), patch.object(
            integration_owner, "_pid_is_running", return_value=True
        ), patch.object(
            integration_owner, "_looks_like_anam_server_process", return_value=True
        ), patch.object(
            integration_owner, "_terminate_existing_owner", return_value=True
        ):
            claimed = integration_owner.claim_integration_ownership()

        self.assertTrue(claimed["owner"])
        self.assertEqual(claimed["reason"], "took_over_existing_instance")
        self.assertNotEqual(claimed["metadata"].get("instance_id"), stale["instance_id"])

    def test_windows_pid_helper_requires_still_active_exit_code(self):
        fake_kernel32 = SimpleNamespace(
            OpenProcess=lambda _access, _inherit, _pid: 384,
            GetExitCodeProcess=lambda _handle, exit_code_ptr: setattr(exit_code_ptr._obj, "value", 2) or 1,
            CloseHandle=lambda _handle: 1,
        )
        fake_ctypes = SimpleNamespace(
            windll=SimpleNamespace(kernel32=fake_kernel32),
            c_ulong=lambda: SimpleNamespace(value=0),
            byref=lambda obj: SimpleNamespace(_obj=obj),
        )

        with patch.object(integration_owner.os, "name", "nt"), patch.dict("sys.modules", {"ctypes": fake_ctypes}):
            self.assertFalse(integration_owner._pid_is_running(119016))
