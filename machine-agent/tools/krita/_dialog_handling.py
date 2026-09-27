"""
Dialog handling for Krita MCP save/export operations.

Krita pops modal dialogs ("PNG image - Krita", "JPEG image - Krita", etc.) for
format-specific export options. These block the plugin's HTTP response until
the user dismisses them. Boys painting via MCP can't see or click them, so the
call hangs.

This module provides send_command_with_dialog_handling() — a drop-in for the
direct httpx.post call in server.py that runs the request on a background
thread and watches for known-shaped dialogs in parallel, dismissing them with
Enter (which accepts default export options).

Whitelist-only: the regex EXPORT_DIALOG_RE is intentionally narrow. Anything
outside it is left alone.

Dependencies: pygetwindow, pyautogui — already installed for the machine-agent.
"""
from __future__ import annotations

import re
import time
import threading
from typing import Optional

import httpx

EXPORT_DIALOG_RE = re.compile(r"^[A-Za-z0-9]+ image - Krita$")

POLL_INTERVAL = 0.3
DISMISS_SETTLE = 0.3
ACTIVATE_SETTLE = 0.2


def _find_export_dialogs():
    """Return pygetwindow Window objects whose title matches the export-dialog pattern."""
    try:
        import pygetwindow as gw
    except ImportError:
        return []
    out = []
    for w in gw.getAllWindows():
        title = w.title or ""
        if EXPORT_DIALOG_RE.match(title):
            out.append(w)
    return out


def _dialog_still_up(title: str) -> bool:
    """Re-check whether a dialog with this exact title is still present."""
    try:
        import pygetwindow as gw
        return bool(gw.getWindowsWithTitle(title))
    except Exception:
        return False


def _dismiss_via_postmessage(window) -> bool:
    """Send VK_RETURN directly to the window's message queue via Win32 PostMessage.

    This bypasses Windows focus protection — works whether or not the calling
    process can steal foreground focus. Required for reliable operation from
    a background thread inside machine-agent.

    Returns True only if the dialog is verifiably gone afterwards.
    """
    try:
        import win32api
        import win32con
        hwnd = getattr(window, "_hWnd", None)
        if not hwnd:
            return False
        title = window.title
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        time.sleep(DISMISS_SETTLE)
        return not _dialog_still_up(title)
    except Exception:
        return False


def _dismiss_via_focus(window) -> bool:
    """Fallback: try to activate the window and use pyautogui.press('enter').

    Only effective when this process can take foreground focus. May fail
    silently on Windows 11 if focus protection blocks activation.
    """
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        title = window.title
        window.activate()
        time.sleep(ACTIVATE_SETTLE)
        pyautogui.press("enter")
        time.sleep(DISMISS_SETTLE)
        return not _dialog_still_up(title)
    except Exception:
        return False


def _dismiss_dialog(window) -> bool:
    """Try to dismiss the dialog. Returns True only if the dialog is verifiably gone.

    Strategy: PostMessage first (focus-free, works from background threads),
    then activate+press as fallback for environments without pywin32.
    """
    if _dismiss_via_postmessage(window):
        return True
    return _dismiss_via_focus(window)


def send_command_with_dialog_handling(
    krita_url: str,
    action: str,
    params: Optional[dict] = None,
    timeout: float = 60.0,
) -> dict:
    """Like a direct httpx.post to Krita, but watches for known export dialogs and
    dismisses them with Enter while the request is in flight.

    Returns the parsed JSON response from Krita on success, or a dict with an
    'error' key on failure. Adds 'dismissed_dialogs' (list[str]) when any
    dialogs were handled.
    """
    if params is None:
        params = {}

    holder: dict = {}

    def runner():
        try:
            r = httpx.post(
                krita_url,
                json={"action": action, "params": params},
                timeout=timeout,
            )
            holder["status"] = r.status_code
            try:
                holder["result"] = r.json()
            except Exception:
                holder["result"] = {"error": f"Non-JSON response: {r.text[:200]}"}
        except httpx.ConnectError:
            holder["result"] = {
                "error": "Cannot connect to Krita. Is Krita running with the MCP plugin enabled?"
            }
        except httpx.TimeoutException:
            holder["result"] = {
                "error": f"Operation timed out after {timeout}s."
            }
        except Exception as e:
            holder["result"] = {"error": f"{type(e).__name__}: {e}"}

    t = threading.Thread(target=runner, daemon=True)
    t.start()

    dismissed: list[str] = []
    deadline = time.time() + timeout

    while t.is_alive() and time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        for w in _find_export_dialogs():
            title = w.title
            # Always retry if a matching dialog is currently visible — only
            # record success on verified dismissal. A previous "successful"
            # call may have failed silently due to focus protection, leaving
            # the dialog up; we keep trying every poll until it's actually gone.
            if _dismiss_dialog(w):
                if title not in dismissed:
                    dismissed.append(title)

    t.join(timeout=2.0)

    result = holder.get("result", {"error": "Request thread did not return a result."})
    if dismissed:
        if isinstance(result, dict):
            result = dict(result)
            result["dismissed_dialogs"] = dismissed
    return result
