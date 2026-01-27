# Celestial Weather MCP Server

Get weather, moon phases, seasons, sunrise/sunset times, air quality, and astronomical events - all in one tool!

**No API keys required!** Uses free Open-Meteo APIs.

## What It Does

- **Weather** - Current conditions and 7-day forecast
- **Moon** - Phase, illumination, next full moon
- **Season** - Current season, days until next
- **Sun** - Sunrise, sunset, day length, golden hour
- **Air Quality** - AQI, PM2.5, health category
- **Meteor Showers** - Upcoming showers with peak dates

---

## Quick Start

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run the Server

```bash
python run_server.py
```

That's it! No API keys needed.

### Connect to Claude

Add to your MCP settings:

```json
{
  "mcpServers": {
    "weather": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `get_celestial_overview` | **Main tool** - Get everything in one call |
| `set_default_location` | Set your default city |
| `set_units` | Switch between metric/imperial |
| `save_location` | Save a location alias (e.g., "home") |
| `list_saved_locations` | View saved locations |

---

## Example Usage

**First time setup:**
```
"Set my default location to Chicago"
"Use imperial units"
```

**Daily use:**
```
"What's the weather?"
"Is it a good night for stargazing?"
"When's the next full moon?"
```

**Multiple locations:**
```
"Save London as mom's place"
"What's the weather at mom's place?"
```

---

## What `get_celestial_overview` Returns

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

## Configuration

Settings are stored in `~/.config/celestial-weather/config.json`

You can:
- Set a default location (so you don't have to type it every time)
- Choose metric (°C, km/h) or imperial (°F, mph) units
- Save location aliases for quick access

---

## Data Sources

- **Weather**: [Open-Meteo](https://open-meteo.com/) (free, no key)
- **Air Quality**: [Open-Meteo Air Quality](https://open-meteo.com/en/docs/air-quality-api) (free, no key)
- **Astronomy**: [PyEphem](https://rhodesmill.org/pyephem/) (local calculations)

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
