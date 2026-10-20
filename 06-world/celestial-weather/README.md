# Celestial Weather MCP

Get weather, air quality, sun times, moon information and seasons. Online weather and geocoding need internet access; no API key is required.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\celestial-weather`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\celestial-weather"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Choose your location

There is no populated location file to edit. After connecting, call `set_default_location` with your city (include the country if ambiguous). You can also pass a city directly to `get_celestial_overview` without saving it. The server writes preferences to its package-local config file.

Call `set_units` with `metric` or `imperial` to choose units. The astronomy calculations run locally. These instructions use `server.py` for stdio; `run_server.py` instead starts an HTTP listener on all interfaces and is not needed for local setup.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "celestial-weather": {
      "command": "C:/MCP-Starter/Sharing-MCPs/celestial-weather/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/celestial-weather/server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask: `Use get_celestial_overview for London, United Kingdom.` Confirm the returned location. Then set your own default city and try the overview without a city.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **No default location:** pass `city` or call `set_default_location` first.
- **Wrong city:** include region/country and inspect the returned coordinates.
- **Timezone error on Windows:** reinstall requirements; `tzdata` is included.
- **Weather request fails:** check internet access; upstream availability is separate from local startup.

## Available Tools

| Tool | Description |
|------|-------------|
| `get_celestial_overview` | **Main tool** - Get everything in one call |
| `set_default_location` | Set your default city |
| `set_units` | Switch between metric/imperial |
| `save_location` | Save a location alias (e.g., "home") |
| `list_saved_locations` | View saved locations |

---

## What `get_celestial_overview` Returns

This is a fictional formatting example, not a current weather report. Your tool result uses the actual requested location and date.

```
Celestial Overview for Chicago, United States
==================================================

WEATHER
-------
Partly cloudy
Temperature: 72.5°F (feels like 74.1°F)
Humidity: 45% | Wind: 8.2 mph
Cloud Cover: 35% | UV Index: 6

AIR QUALITY
-----------
AQI: 42 (Good)
PM2.5: 8.2 µg/m³

MOON
----
Phase: Waxing Gibbous (78.5% illuminated)
Next Full Moon: 2024-01-25 17:54 UTC

SEASON
------
Winter (Northern Hemisphere)
Spring begins in 52 days (2024-03-19)

SUN
---
Sunrise: 07:12 | Sunset: 16:58
Day Length: 9h 46m
Golden Hour Evening: 15:58 - 16:58

UPCOMING METEOR SHOWERS
----------------------
Quadrantids: 2024-01-03 (2 days) - ~120 meteors/hour

7-DAY FORECAST
--------------
2024-01-01: Partly cloudy | High: 38.5°F Low: 28.2°F | Rain: 10%
...
```

---

## Data Sources

- **Weather**: [Open-Meteo](https://open-meteo.com/) (free, no key)
- **Air Quality**: [Open-Meteo Air Quality](https://open-meteo.com/en/docs/air-quality-api) (free, no key)
- **Astronomy**: [PyEphem](https://rhodesmill.org/pyephem/) (local calculations)

---

## Reading the code

Start with [run_server.py](run_server.py), then [server.py](server.py). The installation steps above describe the runtime configuration.
