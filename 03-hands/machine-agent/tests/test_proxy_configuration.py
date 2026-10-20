import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from proxy_client import MachineAgentClient, MachineAgentError


def test_missing_key_has_no_hidden_file_or_network_fallback():
    with patch.dict(os.environ, {}, clear=True), patch("proxy_client.Path.read_text") as read_key:
        client = MachineAgentClient.from_env()
        read_key.assert_not_called()
        with patch("proxy_client.request.urlopen") as send:
            with pytest.raises(MachineAgentError, match="MACHINE_AGENT_API_KEY"):
                client.invoke("fs_read_file", {"path": "example.txt"})
            send.assert_not_called()


def test_explicit_key_file_and_environment_precedence():
    with tempfile.TemporaryDirectory() as folder:
        key = Path(folder) / "bridge.key"
        key.write_text("test-key\n", encoding="utf-8")
        with patch.dict(os.environ, {"MACHINE_AGENT_KEY_FILE": str(key)}, clear=True):
            assert MachineAgentClient.from_env().api_key == "test-key"
        with patch.dict(os.environ, {"MACHINE_AGENT_API_KEY": "direct-key", "MACHINE_AGENT_KEY_FILE": str(key)}, clear=True):
            with patch("proxy_client.Path.read_text") as read_key:
                assert MachineAgentClient.from_env().api_key == "direct-key"
                read_key.assert_not_called()
