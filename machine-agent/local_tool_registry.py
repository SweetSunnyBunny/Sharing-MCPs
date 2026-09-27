from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolBinding:
    module_path: Path
    function_name: str


PACKAGE_ROOT = Path(__file__).resolve().parent
UI_ROOT = Path(os.environ.get("ANAM_ROOT", str(PACKAGE_ROOT.parent / "ui")))

OBSIDIAN_ADAPTER = Path(__file__).resolve().parent / "obsidian_adapter.mjs"


LOCAL_TOOLS: dict[str, ToolBinding] = {
    "anam_result": ToolBinding(module_path=UI_ROOT / "scripts/anam_gateway_mcp.py", function_name="anam_result"),
    "anam_discover": ToolBinding(module_path=UI_ROOT / "scripts/anam_gateway_mcp.py", function_name="anam_discover"),
    "anam_invoke": ToolBinding(module_path=UI_ROOT / "scripts/anam_gateway_mcp.py", function_name="anam_invoke"),
    "anam_job": ToolBinding(module_path=UI_ROOT / "scripts/anam_gateway_mcp.py", function_name="anam_job"),
    "clipboard_read": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/clipboard/clipboard_server.py',
        function_name="clipboard_read",
    ),
    "clipboard_write": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/clipboard/clipboard_server.py',
        function_name="clipboard_write",
    ),
    "clipboard_history": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/clipboard/clipboard_server.py',
        function_name="clipboard_history",
    ),
    "clipboard_search": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/clipboard/clipboard_server.py',
        function_name="clipboard_search",
    ),
    "screenshot": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="screenshot",
    ),
    "get_screen_size": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="get_screen_size",
    ),
    "get_mouse_position": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="get_mouse_position",
    ),
    "mouse_move": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="mouse_move",
    ),
    "mouse_click": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="mouse_click",
    ),
    "mouse_scroll": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="mouse_scroll",
    ),
    "type_text": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="type_text",
    ),
    "hotkey": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="hotkey",
    ),
    "key_press": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="key_press",
    ),
    "list_windows": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="list_windows",
    ),
    "focus_window": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="focus_window",
    ),
    "locate_on_screen": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/desktop-control/desktop_control_server.py',
        function_name="locate_on_screen",
    ),
    "terminal_execute": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/terminal/terminal_server.py',
        function_name="terminal_execute",
    ),
    "terminal_create": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/terminal/terminal_server.py',
        function_name="terminal_create",
    ),
    "terminal_list": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/terminal/terminal_server.py',
        function_name="terminal_list",
    ),
    "terminal_destroy": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/terminal/terminal_server.py',
        function_name="terminal_destroy",
    ),
    "terminal_get_info": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/terminal/terminal_server.py',
        function_name="terminal_get_info",
    ),
    "fs_list_directory": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="list_directory",
    ),
    "fs_create_directory": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="create_directory",
    ),
    "fs_read_file": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="read_file",
    ),
    "fs_read_image": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="read_image",
    ),
    "fs_get_file_info": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="get_file_info",
    ),
    "fs_write_file": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="write_file",
    ),
    "fs_edit_file": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="edit_file",
    ),
    "fs_write_binary": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="write_binary",
    ),
    "fs_copy": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="copy_file",
    ),
    "fs_move": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="move_file",
    ),
    "fs_delete": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="delete_file",
    ),
    "fs_search": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="search_files",
    ),
    "fs_search_content": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="search_content",
    ),
    "fs_list_drives": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="list_drives",
    ),
    "fs_get_recent_files": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/filesystem/server.py',
        function_name="get_recent_files",
    ),
    "krita_health": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_health",
    ),
    "krita_new_canvas": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_new_canvas",
    ),
    "krita_set_color": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_set_color",
    ),
    "krita_set_brush": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_set_brush",
    ),
    "krita_stroke": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_stroke",
    ),
    "krita_fill": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_fill",
    ),
    "krita_draw_shape": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_draw_shape",
    ),
    "krita_export": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_export",
    ),
    "krita_save": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_save",
    ),
    "krita_save_as": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_save_as",
    ),
    "krita_request_save": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_request_save",
    ),
    "krita_undo": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_undo",
    ),
    "krita_redo": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_redo",
    ),
    "krita_clear": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_clear",
    ),
    "krita_get_color_at": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_get_color_at",
    ),
    "krita_list_brushes": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_list_brushes",
    ),
    "krita_gradient": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_gradient",
    ),
    "krita_text": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_text",
    ),
    "krita_flood_fill": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_flood_fill",
    ),
    "krita_new_layer": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_new_layer",
    ),
    "krita_delete_layer": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_delete_layer",
    ),
    "krita_list_layers": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_list_layers",
    ),
    "krita_set_layer_opacity": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_set_layer_opacity",
    ),
    "krita_select_layer": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_select_layer",
    ),
    "krita_duplicate_layer": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_duplicate_layer",
    ),
    "krita_merge_down": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_merge_down",
    ),
    "krita_transform": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_transform",
    ),
    "krita_select_rectangle": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_select_rectangle",
    ),
    "krita_select_ellipse": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_select_ellipse",
    ),
    "krita_select_all": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_select_all",
    ),
    "krita_deselect": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_deselect",
    ),
    "krita_invert_selection": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_invert_selection",
    ),
    "krita_filter": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_filter",
    ),
    "krita_get_document_info": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_get_document_info",
    ),
    "krita_resize_canvas": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_resize_canvas",
    ),
    "krita_crop_to_selection": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_crop_to_selection",
    ),
    "krita_bezier_curve": ToolBinding(
        module_path=PACKAGE_ROOT / 'tools/krita/server.py',
        function_name="krita_bezier_curve",
    ),
}


OBSIDIAN_TOOLS = {
    "read_note",
    "write_note",
    "append_to_note",
    "delete_note",
    "move_note",
    "list_notes",
    "list_folders",
    "create_folder",
    "search_notes",
    "search_by_tag",
    "get_recent_notes",
    "list_tags",
    "get_backlinks",
    "get_outgoing_links",
    "get_frontmatter",
    "update_frontmatter",
    "list_templates",
    "create_from_template",
    "create_daily_note",
    "add_journal_entry",
    "get_vault_stats",
    "get_vault_path",
}


_module_cache: dict[Path, Any] = {}
_module_locks: dict[Path, threading.Lock] = {}
_module_cache_lock = threading.Lock()


def _lock_for_module(module_path: Path) -> threading.Lock:
    """Return a lock scoped to one adapter module.

    Adapter imports can perform third-party initialization.  A single global
    import lock meant one slow or wedged adapter import blocked every unrelated
    machine-agent tool until Cloudflare returned a 524.  Keep duplicate imports
    serialized without coupling filesystem, terminal, Anam, and the other
    adapters to one another.
    """
    with _module_cache_lock:
        lock = _module_locks.get(module_path)
        if lock is None:
            lock = threading.Lock()
            _module_locks[module_path] = lock
        return lock


def _load_module(module_path: Path) -> Any:
    with _module_cache_lock:
        cached = _module_cache.get(module_path)
        if cached is not None:
            return cached

    module_lock = _lock_for_module(module_path)
    with module_lock:
        # Another request may have completed this specific import while we
        # waited for its lock.
        with _module_cache_lock:
            cached = _module_cache.get(module_path)
            if cached is not None:
                return cached

        module_name = f"machine_agent_{module_path.stem}_{abs(hash(str(module_path)))}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load module: {module_path}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with _module_cache_lock:
            _module_cache[module_path] = module
        return module


def list_tools() -> list[str]:
    return sorted(set(LOCAL_TOOLS.keys()) | OBSIDIAN_TOOLS)


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(v) for v in value]
    if isinstance(value, bytes):
        return {
            "type": "bytes",
            "encoding": "utf-8",
            "data": value.decode("utf-8", errors="replace"),
        }
    if hasattr(value, "to_image_content"):
        content = value.to_image_content()
        return {
            "type": "image",
            "data": content.data,
            "mimeType": content.mimeType,
        }
    if hasattr(value, "path"):
        return {
            "type": "image",
            "path": str(getattr(value, "path")),
        }
    try:
        json.dumps(value)
        return value
    except TypeError:
        return {
            "type": value.__class__.__name__,
            "repr": repr(value),
        }


def _invoke_obsidian_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    process = subprocess.run(
        ["node", str(OBSIDIAN_ADAPTER), tool_name, json.dumps(arguments)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    output = process.stdout.strip()
    if not output:
        stderr = process.stderr.strip()
        raise RuntimeError(stderr or "Obsidian adapter produced no output")

    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid Obsidian adapter output: {output}") from exc

    if payload.get("ok"):
        return payload.get("result")

    raise RuntimeError(payload.get("error", "Unknown Obsidian adapter error"))


class AsyncExecutor:
    def __init__(self) -> None:
        import asyncio

        self._asyncio = asyncio
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="machine-agent-async")
        self._thread.start()

    def _run_loop(self) -> None:
        self._asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Any) -> Any:
        future = self._asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()


_async_executor = AsyncExecutor()


def invoke_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    if tool_name in OBSIDIAN_TOOLS:
        return _serialize(_invoke_obsidian_tool(tool_name, arguments))

    binding = LOCAL_TOOLS.get(tool_name)
    if binding is None:
        raise KeyError(f"Unknown tool '{tool_name}'")

    module = _load_module(binding.module_path)
    function = getattr(module, binding.function_name, None)
    if function is None:
        raise RuntimeError(
            f"Tool '{tool_name}' is mapped to missing function "
            f"'{binding.function_name}' in {binding.module_path}"
        )

    if not callable(function) and hasattr(function, "fn"):
        function = function.fn

    result = function(**arguments)
    if inspect.isawaitable(result):
        result = _async_executor.run(result)
    return _serialize(result)
