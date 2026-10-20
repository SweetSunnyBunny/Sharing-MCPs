from pathlib import Path
from unittest.mock import patch

from services.codex_cli import find_codex_executable


def test_npm_shim_resolves_its_native_binary(tmp_path):
    shim = tmp_path / "codex.cmd"
    shim.touch()
    native = tmp_path / "node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe"
    native.parent.mkdir(parents=True)
    native.touch()
    with patch("services.codex_cli.shutil.which", return_value=str(shim)), patch("services.codex_cli.platform.machine", return_value="AMD64"):
        assert find_codex_executable() == str(native)


def test_other_installations_keep_their_launcher(tmp_path):
    for name in ("codex.cmd", "codex.exe", "codex"):
        shim = tmp_path / name
        with patch("services.codex_cli.shutil.which", return_value=str(shim)):
            assert find_codex_executable() == str(shim)
