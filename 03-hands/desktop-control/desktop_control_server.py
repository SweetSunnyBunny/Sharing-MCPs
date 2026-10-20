"""Desktop Control MCP server — mouse, keyboard, screenshots, window management."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pyautogui
from fastmcp import FastMCP

mcp = FastMCP("desktop-control")

# Safety: prevent pyautogui from moving too fast
pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = True  # move mouse to corner to abort

# Prefer the existing anam images directory when available, but fall back locally.
LEGACY_SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"
LOCAL_SCREENSHOTS_DIR = Path(__file__).resolve().parent / "screenshots"
SCREENSHOTS_DIR = Path(os.environ.get("DESKTOP_CONTROL_SCREENSHOTS_DIR", str(LEGACY_SCREENSHOTS_DIR)))
PUBLIC_BASE_URL = os.environ.get("DESKTOP_CONTROL_PUBLIC_BASE_URL", "").rstrip("/")


def _candidate_screenshot_dirs() -> list[Path]:
    """Try the configured directory first, then fall back locally if needed."""
    candidates = [SCREENSHOTS_DIR]
    if LOCAL_SCREENSHOTS_DIR != SCREENSHOTS_DIR:
        candidates.append(LOCAL_SCREENSHOTS_DIR)
    return candidates


def screenshot(region: str | None = None) -> str:
    """Take a screenshot of the screen or a region. Saves as JPEG and returns the file path and URL.

    Args:
        region: Optional region as "x,y,width,height" (e.g. "100,200,800,600").
                If omitted, captures the full screen.
    """
    try:
        if region:
            parts = [int(x.strip()) for x in region.split(",")]
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
                    f"Screenshot saved: {filepath}\n"
                    f"Size: {img.width}x{img.height}, {file_size // 1024} KB"
                ]
                if PUBLIC_BASE_URL and output_dir == SCREENSHOTS_DIR:
                    lines.append(f"URL: {PUBLIC_BASE_URL}/api/images/file/{filename}")
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
    """Move the mouse cursor to a specific position.

    Args:
        x: X coordinate.
        y: Y coordinate.
    """
    pyautogui.moveTo(x, y, duration=0.3)
    return f"Mouse moved to ({x}, {y})"


def mouse_click(x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> str:
    """Click the mouse at a position (or current position if no coordinates given).

    Args:
        x: X coordinate (optional, uses current position if omitted).
        y: Y coordinate (optional, uses current position if omitted).
        button: Mouse button — "left", "right", or "middle".
        clicks: Number of clicks (default 1, use 2 for double-click).
    """
    kwargs = {"button": button, "clicks": clicks}
    if x is not None and y is not None:
        kwargs["x"] = x
        kwargs["y"] = y
    pyautogui.click(**kwargs)
    pos = f"({x}, {y})" if x is not None else "current position"
    return f"Clicked {button} {clicks}x at {pos}"


def mouse_scroll(amount: int, x: int | None = None, y: int | None = None) -> str:
    """Scroll the mouse wheel.

    Args:
        amount: Scroll amount (positive = up, negative = down).
        x: X coordinate to scroll at (optional).
        y: Y coordinate to scroll at (optional).
    """
    if x is not None and y is not None:
        pyautogui.scroll(amount, x=x, y=y)
    else:
        pyautogui.scroll(amount)
    direction = "up" if amount > 0 else "down"
    return f"Scrolled {direction} by {abs(amount)}"


def type_text(text: str, interval: float = 0.02) -> str:
    """Type text using the keyboard (simulates keystrokes).

    Args:
        text: The text to type.
        interval: Delay between keystrokes in seconds (default 0.02).
    """
    pyautogui.typewrite(text, interval=interval)
    return f"Typed {len(text)} characters"


def hotkey(keys: str) -> str:
    """Press a keyboard shortcut (e.g. "ctrl+c", "alt+tab", "win+d").

    Args:
        keys: Keys to press together, separated by "+" (e.g. "ctrl+c", "alt+f4").
    """
    key_list = [k.strip() for k in keys.split("+")]
    pyautogui.hotkey(*key_list)
    return f"Pressed: {'+'.join(key_list)}"


def key_press(key: str) -> str:
    """Press a single key (enter, tab, escape, f1, etc.).

    Args:
        key: The key to press (e.g. "enter", "tab", "escape", "f5", "space").
    """
    pyautogui.press(key)
    return f"Pressed: {key}"


def list_windows() -> str:
    """List all visible windows with their titles and positions."""
    try:
        import pygetwindow as gw
        windows = gw.getAllWindows()
        visible = [w for w in windows if w.title.strip() and w.visible]
        if not visible:
            return "No visible windows found."
        lines = []
        for w in visible[:30]:
            lines.append(
                f"  '{w.title}' — pos:({w.left},{w.top}) size:{w.width}x{w.height}"
            )
        return f"Visible windows ({len(visible)}):\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing windows: {e}"


def focus_window(title: str) -> str:
    """Bring a window to the foreground by its title (partial match).

    Args:
        title: Window title or partial match.
    """
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
    """Find an image on screen and return its position. Useful for finding buttons or UI elements.

    Args:
        image_path: Path to the image file to find on screen.
        confidence: Match confidence 0-1 (default 0.8). Lower = more lenient.
    """
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
