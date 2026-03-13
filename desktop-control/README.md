# desktop-control

Desktop control MCP server for mouse, keyboard, screenshots, and basic window management on Windows.

## Features

- Capture full-screen or regional screenshots
- Read screen size and mouse position
- Move, click, and scroll the mouse
- Type text and send key presses or hotkeys
- List visible windows and focus a matching window
- Locate an image on screen

## Requirements

- Windows desktop session
- Python 3.11+
- An MCP client that supports stdio servers

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python desktop_control_server.py
```

## Optional environment variables

- `DESKTOP_CONTROL_SCREENSHOTS_DIR`
  Use a custom screenshot output directory. Defaults to `./screenshots`.
- `DESKTOP_CONTROL_PUBLIC_BASE_URL`
  If set, screenshot responses will also include a public URL for the saved image.

## Notes

- `pyautogui.FAILSAFE` is enabled. Move the mouse to the top-left corner to abort.
- `locate_on_screen(..., confidence=...)` typically needs OpenCV installed.
