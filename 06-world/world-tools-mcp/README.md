# World Tools MCP

Tools for time, calendar information, weather, public web-page text and public image URLs. Weather uses Open-Meteo; no API key is required.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\world-tools-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\world-tools-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Decide whether to configure home weather

Time and city-specific weather work immediately. Home weather needs your own coordinates. Add `WT_HOME_LAT`, `WT_HOME_LON` and `WT_HOME_LABEL` to the client's `env` object if you want that shortcut. Coordinates must be decimal-number strings. Without them the defaults are 0,0, a placeholder rather than your home.

No `.env` file is loaded automatically. Online tools need internet access. `wt_web_read_url` extracts public HTML; it does not execute JavaScript or sign into websites.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\run_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "world-tools-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/world-tools-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/world-tools-mcp/run_server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `wt_time_now` with timezone `UTC`, then `wt_weather_current` for a chosen city. Confirm the timezone and location in the results.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Wrong home weather:** set your own latitude/longitude or use city-specific weather.
- **Private/local URL rejected:** URL tools accept public internet destinations, not localhost or LAN addresses.
- **Incomplete website text:** JavaScript-only content needs a browser tool.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WT_HOME_LAT` | `0.0` | Home latitude for quick weather checks |
| `WT_HOME_LON` | `0.0` | Home longitude for quick weather checks |
| `WT_HOME_LABEL` | `Home` | Display name for home location |

---

## Available Tools

| Tool | Description |
|------|-------------|
| `wt_time_now` | Current date/time with moon phase in any timezone |
| `wt_calendar_info` | Day-of-week and moon info for any date |
| `wt_weather_current` | Weather + 3-day forecast by city name or home coordinates |
| `wt_weather_home` | Quick weather check using configured home location |
| `wt_web_read_url` | Extract readable text from any public web page |
| `wt_web_view_image_url` | Download and display an image from a URL |

---

## Reading the code

Start with [run_server.py](run_server.py), then [world_tools_server.py](world_tools_server.py). The installation steps above describe the runtime configuration.
