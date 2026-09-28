# Desktop Control MCP

Mouse, keyboard, screenshots and window control for the same Windows desktop where this process runs. An unlocked interactive Windows session is required; a headless server or cloud Worker cannot supply it.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\desktop-control`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\desktop-control"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Prepare the Windows desktop

Keep the desktop signed in and unlocked. No API key or separate app plugin is required. Start with the read-only screen-size test below before trying mouse or keyboard actions.

Screenshots default to this package's `screenshots` directory. Optional client environment settings are `DESKTOP_CONTROL_SCREENSHOTS_DIR` (an absolute writable directory) and `DESKTOP_CONTROL_PUBLIC_BASE_URL` (only if you separately publish those images). A public URL is not needed for local use. No `.env` file is loaded automatically.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\desktop_control_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "desktop-control": {
      "command": "C:/MCP-Starter/Sharing-MCPs/desktop-control/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/desktop-control/desktop_control_server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `get_screen_size`, then `get_mouse_position`. Success returns numbers for your active desktop. A later `screenshot` call should return a local image path.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **No windows or black screenshots:** run in the logged-in Windows desktop, not a service account or locked remote session.
- **PyAutoGUI fail-safe:** moving the pointer to a screen corner can stop automation; move it away before another deliberate action.
- **Wrong click coordinates:** display scaling and multiple monitors can affect coordinates; obtain a fresh screenshot first.

## Features

- Capture full-screen or regional screenshots
- Read screen size and mouse position
- Move, click, and scroll the mouse
- Type text and send key presses or hotkeys
- List visible windows and focus a matching window
- Locate an image on screen

## Reading the code

Start with [desktop_control_server.py](desktop_control_server.py). The installation steps above describe the runtime configuration.
