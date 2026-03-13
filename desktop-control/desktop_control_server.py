"""Desktop Control MCP server for mouse, keyboard, screenshots, and window management."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pyautogui
from fastmcp import FastMCP

mcp = FastMCP("desktop-control")

pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = True

LOCAL_SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"
SCREENSHOTS_DIR = Path(os.environ.get("DESKTOP_CONTROL_SCREENSHOTS_DIR", str(LOCAL_SCREENSHOTS_DIR)))
PUBLIC_BASE_URL = os.environ.get("DESKTOP_CONTROL_PUBLIC_BASE_URL", "").rstrip("/")


def _candidate_screenshot_dirs() -> list[Path]:
    candidates = [SCREENSHOTS_DIR]
    if LOCAL_SCREENSHOTS_DIR != SCREENSHOTS_DIR:
        candidates.append(LOCAL_SCREENSHOTS_DIR)
    return candidates


def screenshot(region: str | None = None) -> str:
    """Take a screenshot of the full screen or a region.

    Args:
        region: Optional region as "x,y,width,height".
    """
    try:
        if region:
            parts = [int(x.strip()) for x in region.split(",")]
            if len(parts) != 4:
                return 'Screenshot failed: region must be "x,y,width,height".'
            img = pyautogui.screenshot(region=tuple(parts))
        else:
            img = pyautogui.screenshot()

        filename = f"screenshot_{uuid.uuid4().hex[:8]}.jpg"
        last_error = None
        for output_dir in _candidate_screenshot_dirs():
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
                filepath = output_dir / filename
                img.save(str(filepath), format="JPEG", quality=70)

                file_size = filepath.stat().st_size
                lines = [
                    f"Screenshot saved: {filepath}",
                    f"Size: {img.width}x{img.height}, {file_size // 1024} KB",
                ]
                if PUBLIC_BASE_URL and output_dir == SCREENSHOTS_DIR:
                    lines.append(f"URL: {PUBLIC_BASE_URL}/{filename}")
                return "\n".join(lines)
            except OSError as e:
                last_error = e
        raise last_error or PermissionError("No writable screenshot directory is available.")
    except Exception as e:
        return f"Screenshot failed: {e}"


def get_screen_size() -> str:
    """Get the screen resolution."""
    w, h = pyautogui.size()
    return f"Screen size: {w}x{h}"


def get_mouse_position() -> str:
    """Get the current mouse cursor position."""
    x, y = pyautogui.position()
    return f"Mouse position: ({x}, {y})"


def mouse_move(x: int, y: int) -> str:
    """Move the mouse cursor to a specific position."""
    pyautogui.moveTo(x, y, duration=0.3)
    return f"Mouse moved to ({x}, {y})"


def mouse_click(x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> str:
    """Click the mouse at a position or the current cursor location."""
    kwargs = {"button": button, "clicks": clicks}
    if x is not None and y is not None:
        kwargs["x"] = x
        kwargs["y"] = y
    pyautogui.click(**kwargs)
    pos = f"({x}, {y})" if x is not None and y is not None else "current position"
    return f"Clicked {button} {clicks}x at {pos}"


def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll the mouse wheel."""
    if x is not None and y is not None:
        pyautogui.scroll(amount, x=x, y=y)
    else:
        pyautogui.scroll(amount)
    direction = "up" if amount > 0 else "down"
    return f"Scrolled {direction} by {abs(amount)}"


def type_text(text: str, interval: float = 0.02) -> str:
    """Type text using simulated keystrokes."""
    pyautogui.typewrite(text, interval=interval)
    return f"Typed {len(text)} characters"


def hotkey(keys: str) -> str:
    """Press a keyboard shortcut such as "ctrl+c" or "alt+tab"."""
    key_list = [k.strip() for k in keys.split("+")]
    pyautogui.hotkey(*key_list)
    return f"Pressed: {'+'.join(key_list)}"


def key_press(key: str) -> str:
    """Press a single key."""
    pyautogui.press(key)
    return f"Pressed: {key}"


def list_windows() -> str:
    """List visible windows with titles and positions."""
    try:
        import pygetwindow as gw

        windows = gw.getAllWindows()
        visible = [w for w in windows if w.title.strip() and w.visible]
        if not visible:
            return "No visible windows found."
        lines = []
        for w in visible[:30]:
            lines.append(f"  '{w.title}' - pos:({w.left},{w.top}) size:{w.width}x{w.height}")
        return f"Visible windows ({len(visible)}):\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing windows: {e}"


def focus_window(title: str) -> str:
    """Bring a matching window to the foreground."""
    try:
        import pygetwindow as gw

        windows = gw.getWindowsWithTitle(title)
        if not windows:
            return f"No window found matching '{title}'"
        win = windows[0]
        win.activate()
        return f"Focused window: '{win.title}'"
    except Exception as e:
        return f"Error focusing window: {e}"


def locate_on_screen(image_path: str, confidence: float = 0.8) -> str:
    """Find an image on the screen and return its center position."""
    try:
        location = pyautogui.locateOnScreen(image_path, confidence=confidence)
        if location:
            center = pyautogui.center(location)
            return f"Found at center ({center.x}, {center.y}), region: {location}"
        return "Image not found on screen."
    except Exception as e:
        return f"Error locating image: {e}"


for tool_fn in (
    screenshot,
    get_screen_size,
    get_mouse_position,
    mouse_move,
    mouse_click,
    mouse_scroll,
    type_text,
    hotkey,
    key_press,
    list_windows,
    focus_window,
    locate_on_screen,
):
    mcp.tool()(tool_fn)


if __name__ == "__main__":
    mcp.run(transport="stdio")
