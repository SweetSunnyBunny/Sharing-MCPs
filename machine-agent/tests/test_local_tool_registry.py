from __future__ import annotations

import threading
from pathlib import Path

import local_tool_registry as registry


def test_slow_module_import_does_not_block_unrelated_module(tmp_path: Path) -> None:
    slow_started = threading.Event()
    release_slow = threading.Event()

    slow_module = tmp_path / "slow_adapter.py"
    fast_module = tmp_path / "fast_adapter.py"
    slow_module.write_text("VALUE = 'slow'\n", encoding="utf-8")
    fast_module.write_text("VALUE = 'fast'\n", encoding="utf-8")

    original_exec_module = registry.importlib.util.spec_from_file_location

    def controlled_spec(name: str, path: Path):
        spec = original_exec_module(name, path)
        assert spec is not None and spec.loader is not None
        original_loader = spec.loader

        class Loader:
            def create_module(self, spec):
                create = getattr(original_loader, "create_module", None)
                return create(spec) if create is not None else None

            def exec_module(self, module):
                if Path(path) == slow_module:
                    slow_started.set()
                    assert release_slow.wait(timeout=2)
                original_loader.exec_module(module)

        spec.loader = Loader()
        return spec

    registry.importlib.util.spec_from_file_location = controlled_spec
    registry._module_cache.clear()
    registry._module_locks.clear()
    try:
        slow_thread = threading.Thread(target=registry._load_module, args=(slow_module,))
        slow_thread.start()
        assert slow_started.wait(timeout=1)

        fast_thread = threading.Thread(target=registry._load_module, args=(fast_module,))
        fast_thread.start()
        fast_thread.join(timeout=1)
        assert not fast_thread.is_alive()
        assert registry._module_cache[fast_module].VALUE == "fast"

        release_slow.set()
        slow_thread.join(timeout=1)
        assert not slow_thread.is_alive()
        assert registry._module_cache[slow_module].VALUE == "slow"
    finally:
        release_slow.set()
        registry.importlib.util.spec_from_file_location = original_exec_module
        registry._module_cache.clear()
        registry._module_locks.clear()


def test_blocking_async_tool_does_not_block_other_tools(tmp_path: Path) -> None:
    'An `async def` tool that blocks (disk I/O) must not stall other calls.'
    import time

    adapter = tmp_path / "blocking_adapter.py"
    adapter.write_text(
        "import time\n"
        "async def slow():\n"
        "    time.sleep(1.5)\n"
        "    return 'slow'\n"
        "async def fast():\n"
        "    return 'fast'\n",
        encoding="utf-8",
    )
    registry.LOCAL_TOOLS["_test_slow"] = registry.ToolBinding(module_path=adapter, function_name="slow")
    registry.LOCAL_TOOLS["_test_fast"] = registry.ToolBinding(module_path=adapter, function_name="fast")
    try:
        registry.invoke_tool("_test_fast", {})  # import the module up front

        slow_result: list[str] = []
        slow_thread = threading.Thread(target=lambda: slow_result.append(registry.invoke_tool("_test_slow", {})))
        slow_thread.start()
        time.sleep(0.2)

        started = time.monotonic()
        assert registry.invoke_tool("_test_fast", {}) == "fast"
        assert time.monotonic() - started < 0.5
        assert slow_thread.is_alive()

        slow_thread.join(timeout=5)
        assert slow_result == ["slow"]
    finally:
        registry.LOCAL_TOOLS.pop("_test_slow", None)
        registry.LOCAL_TOOLS.pop("_test_fast", None)


def test_wrong_arguments_are_reported_as_argument_errors(tmp_path: Path) -> None:
    adapter = tmp_path / "typed_adapter.py"
    adapter.write_text(
        "def needs_name(name):\n"
        "    return len(name)\n",
        encoding="utf-8",
    )
    registry.LOCAL_TOOLS["_test_typed"] = registry.ToolBinding(module_path=adapter, function_name="needs_name")
    try:
        # Caller's mistake: wrong parameter name.
        try:
            registry.invoke_tool("_test_typed", {"nmae": "x"})
            raise AssertionError("expected ToolArgumentError")
        except registry.ToolArgumentError:
            pass
        # The tool's own failure: a TypeError raised inside it stays a TypeError.
        try:
            registry.invoke_tool("_test_typed", {"name": 5})
            raise AssertionError("expected TypeError")
        except TypeError:
            pass
    finally:
        registry.LOCAL_TOOLS.pop("_test_typed", None)
