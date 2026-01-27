"""
Celestial Weather MCP Server - Standalone Edition

Get weather, moon phases, seasons, sunrise/sunset times, air quality,
UV index, astronomical events, and stargazing recommendations.

Uses Open-Meteo (free, no API key required) for weather data
and ephem for astronomical calculations.

Built with love for sharing.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import ephem
from fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("celestial-weather")

# Config
CONFIG_PATH = Path.home() / ".config" / "celestial-weather" / "config.json"
DEFAULT_CONFIG = {
    "default_location": None,
    "units": "metric",
    "saved_locations": {},
}

# Caches
_location_cache: dict[str, dict[str, Any]] = {}
_config_cache: dict[str, Any] | None = None

# Weather code descriptions
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

# Major meteor showers
METEOR_SHOWERS = [
    {"name": "Quadrantids", "peak_month": 1, "peak_day": 3, "zhr": 120, "parent": "2003 EH1"},
    {"name": "Lyrids", "peak_month": 4, "peak_day": 22, "zhr": 18, "parent": "Comet Thatcher"},
    {"name": "Eta Aquariids", "peak_month": 5, "peak_day": 6, "zhr": 50, "parent": "Halley's Comet"},
    {"name": "Perseids", "peak_month": 8, "peak_day": 12, "zhr": 100, "parent": "Comet Swift-Tuttle"},
    {"name": "Orionids", "peak_month": 10, "peak_day": 21, "zhr": 20, "parent": "Halley's Comet"},
    {"name": "Leonids", "peak_month": 11, "peak_day": 17, "zhr": 15, "parent": "Comet Tempel-Tuttle"},
    {"name": "Geminids", "peak_month": 12, "peak_day": 14, "zhr": 150, "parent": "3200 Phaethon"},
    {"name": "Ursids", "peak_month": 12, "peak_day": 22, "zhr": 10, "parent": "Comet 8P/Tuttle"},
]


def get_timezone(tz_name: str) -> ZoneInfo:
    """Get timezone with fallback."""
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        mappings = {
            "America/Chicago": "CST6CDT", "America/New_York": "EST5EDT",
            "America/Los_Angeles": "PST8PDT", "America/Denver": "MST7MDT",
            "Europe/London": "GMT0BST", "Europe/Paris": "CET-1CEST",
        }
        fallback = mappings.get(tz_name)
        if fallback:
            try:
                return ZoneInfo(fallback)
            except ZoneInfoNotFoundError:
                pass
        return ZoneInfo("UTC")


def load_config() -> dict[str, Any]:
    """Load configuration."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                _config_cache = {**DEFAULT_CONFIG, **json.load(f)}
                return _config_cache
        except Exception:
            pass
    _config_cache = DEFAULT_CONFIG.copy()
    return _config_cache


def save_config(config: dict[str, Any]) -> None:
    """Save configuration."""
    global _config_cache
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    _config_cache = config


def get_units() -> str:
    return load_config().get("units", "metric")


def format_temp(celsius: float) -> str:
    if get_units() == "imperial":
        return f"{celsius * 9/5 + 32:.1f}°F"
    return f"{celsius:.1f}°C"


def format_speed(kmh: float) -> str:
    if get_units() == "imperial":
        return f"{kmh * 0.621371:.1f} mph"
    return f"{kmh:.1f} km/h"


def format_distance(mm: float) -> str:
    if get_units() == "imperial":
        return f"{mm * 0.0393701:.2f} in"
    return f"{mm:.1f} mm"


async def geocode_city(city: str) -> dict[str, Any]:
    """Convert city name to coordinates."""
    if city.lower() in _location_cache:
        return _location_cache[city.lower()]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "en", "format": "json"}
        )
        response.raise_for_status()
        data = response.json()

    if not data.get("results"):
        raise ValueError(f"City '{city}' not found. Try a more specific name.")

    result = data["results"][0]
    location = {
        "name": result.get("name", city),
        "country": result.get("country", "Unknown"),
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "timezone": result.get("timezone", "UTC"),
        "admin1": result.get("admin1", ""),
    }
    _location_cache[city.lower()] = location
    return location


async def resolve_location(city: str | None) -> dict[str, Any]:
    """Resolve city name to location."""
    config = load_config()
    if not city:
        city = config.get("default_location")
        if not city:
            raise ValueError("No city specified and no default location configured.")
    saved = config.get("saved_locations", {})
    if city.lower() in saved:
        city = saved[city.lower()]
    return await geocode_city(city)


def get_moon_phase_info(date: datetime = None) -> dict[str, Any]:
    """Calculate moon phase."""
    if date is None:
        date = datetime.utcnow()
    ephem_date = ephem.Date(date)
    moon = ephem.Moon(ephem_date)
    phase_percent = moon.phase
    prev_new = ephem.previous_new_moon(ephem_date)
    next_full = ephem.next_full_moon(ephem_date)
    lunar_age = float(ephem_date - prev_new)

    if lunar_age < 1.85:
        phase_name = "New Moon"
    elif lunar_age < 7.38:
        phase_name = "Waxing Crescent"
    elif lunar_age < 9.23:
        phase_name = "First Quarter"
    elif lunar_age < 14.77:
        phase_name = "Waxing Gibbous"
    elif lunar_age < 16.61:
        phase_name = "Full Moon"
    elif lunar_age < 22.15:
        phase_name = "Waning Gibbous"
    elif lunar_age < 23.99:
        phase_name = "Last Quarter"
    else:
        phase_name = "Waning Crescent"

    return {
        "phase_name": phase_name,
        "illumination_percent": round(phase_percent, 1),
        "lunar_age_days": round(lunar_age, 1),
        "next_full_moon": ephem.Date(next_full).datetime().strftime("%Y-%m-%d %H:%M UTC"),
    }


def get_season_info(latitude: float, date: datetime = None) -> dict[str, Any]:
    """Determine current season."""
    if date is None:
        date = datetime.utcnow()
    year = date.year
    is_southern = latitude < 0

    spring_dt = ephem.Date(ephem.next_spring_equinox(f"{year}/1/1")).datetime()
    summer_dt = ephem.Date(ephem.next_summer_solstice(f"{year}/1/1")).datetime()
    fall_dt = ephem.Date(ephem.next_fall_equinox(f"{year}/1/1")).datetime()
    winter_dt = ephem.Date(ephem.next_winter_solstice(f"{year}/1/1")).datetime()
    next_spring_dt = ephem.Date(ephem.next_spring_equinox(f"{year + 1}/1/1")).datetime()

    if date < spring_dt:
        season_north, next_change, next_season_north = "Winter", spring_dt, "Spring"
    elif date < summer_dt:
        season_north, next_change, next_season_north = "Spring", summer_dt, "Summer"
    elif date < fall_dt:
        season_north, next_change, next_season_north = "Summer", fall_dt, "Autumn"
    elif date < winter_dt:
        season_north, next_change, next_season_north = "Autumn", winter_dt, "Winter"
    else:
        season_north, next_change, next_season_north = "Winter", next_spring_dt, "Spring"

    season_map = {"Winter": "Summer", "Spring": "Autumn", "Summer": "Winter", "Autumn": "Spring"}
    current_season = season_map[season_north] if is_southern else season_north
    next_season = season_map[next_season_north] if is_southern else next_season_north

    return {
        "current_season": current_season,
        "hemisphere": "Southern" if is_southern else "Northern",
        "next_season": next_season,
        "next_season_starts": next_change.strftime("%Y-%m-%d"),
        "days_until_next_season": (next_change - date).days,
    }


def get_sun_times(latitude: float, longitude: float, timezone: str, date: datetime = None) -> dict[str, Any]:
    """Calculate sunrise/sunset times."""
    tz = get_timezone(timezone)
    if date is None:
        date = datetime.now(tz)

    observer = ephem.Observer()
    observer.lat, observer.lon = str(latitude), str(longitude)
    observer.elevation = 0
    observer.date = ephem.Date(date.replace(tzinfo=None))
    sun = ephem.Sun()

    def to_local(ephem_date):
        return ephem.Date(ephem_date).datetime().replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    try:
        sunrise = to_local(observer.next_rising(sun))
        sunset = to_local(observer.next_setting(sun))
        observer.date = ephem.Date(date.replace(tzinfo=None, hour=0, minute=0, second=0))
        day_sunrise = to_local(observer.next_rising(sun))
        day_sunset = to_local(observer.next_setting(sun))
        day_length = (day_sunset - day_sunrise).total_seconds() / 3600

        return {
            "sunrise": sunrise.strftime("%H:%M"),
            "sunset": sunset.strftime("%H:%M"),
            "day_length": f"{int(day_length)}h {int((day_length % 1) * 60)}m",
            "golden_hour_evening": f"{(sunset - timedelta(hours=1)).strftime('%H:%M')} - {sunset.strftime('%H:%M')}",
            "timezone": timezone,
        }
    except ephem.AlwaysUpError:
        return {"status": "Polar day - sun does not set", "timezone": timezone}
    except ephem.NeverUpError:
        return {"status": "Polar night - sun does not rise", "timezone": timezone}


async def get_weather(latitude: float, longitude: float, timezone: str) -> dict[str, Any]:
    """Fetch weather from Open-Meteo."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude, "longitude": longitude,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,cloud_cover,wind_speed_10m,uv_index",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
                "timezone": timezone, "forecast_days": 7,
            }
        )
        response.raise_for_status()
        data = response.json()

    current = data.get("current", {})
    daily = data.get("daily", {})

    forecast = []
    if daily.get("time"):
        for i in range(min(7, len(daily["time"]))):
            forecast.append({
                "date": daily["time"][i],
                "conditions": WEATHER_CODES.get(daily["weather_code"][i], "Unknown"),
                "high": daily["temperature_2m_max"][i],
                "low": daily["temperature_2m_min"][i],
                "precipitation_chance": daily["precipitation_probability_max"][i],
            })

    return {
        "current": {
            "conditions": WEATHER_CODES.get(current.get("weather_code", 0), "Unknown"),
            "temperature": current.get("temperature_2m", 0),
            "feels_like": current.get("apparent_temperature", 0),
            "humidity": current.get("relative_humidity_2m", 0),
            "cloud_cover": current.get("cloud_cover", 0),
            "wind_speed": current.get("wind_speed_10m", 0),
            "precipitation": current.get("precipitation", 0),
            "uv_index": current.get("uv_index", 0),
        },
        "forecast": forecast,
    }


async def get_air_quality(latitude: float, longitude: float) -> dict[str, Any]:
    """Fetch air quality from Open-Meteo."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": latitude, "longitude": longitude,
                "current": "european_aqi,us_aqi,pm10,pm2_5",
            }
        )
        response.raise_for_status()
        data = response.json()

    current = data.get("current", {})
    us_aqi = current.get("us_aqi", 0)

    if us_aqi <= 50:
        category = "Good"
    elif us_aqi <= 100:
        category = "Moderate"
    elif us_aqi <= 150:
        category = "Unhealthy for Sensitive Groups"
    elif us_aqi <= 200:
        category = "Unhealthy"
    else:
        category = "Very Unhealthy"

    return {
        "us_aqi": us_aqi,
        "category": category,
        "pm2_5": current.get("pm2_5", 0),
        "pm10": current.get("pm10", 0),
    }


def get_upcoming_meteor_showers(days_ahead: int = 60) -> list[dict[str, Any]]:
    """Get upcoming meteor showers."""
    today = datetime.utcnow()
    upcoming = []

    for shower in METEOR_SHOWERS:
        for year in [today.year, today.year + 1]:
            try:
                peak_date = datetime(year, shower["peak_month"], shower["peak_day"])
                days_until = (peak_date - today).days
                if 0 <= days_until <= days_ahead:
                    upcoming.append({
                        "name": shower["name"],
                        "peak_date": peak_date.strftime("%Y-%m-%d"),
                        "days_until": days_until,
                        "expected_rate": f"~{shower['zhr']} meteors/hour",
                        "parent_body": shower["parent"],
                    })
            except ValueError:
                continue

    return sorted(upcoming, key=lambda x: x["days_until"])


# =============================================================================
# MCP TOOLS
# =============================================================================

@mcp.tool()
async def set_default_location(city: str = Field(..., description="City name")) -> dict:
    """Set your default location."""
    location = await geocode_city(city)
    config = load_config()
    config["default_location"] = city
    save_config(config)
    return {"success": True, "message": f"Default set to {location['name']}, {location['country']}"}


@mcp.tool()
async def set_units(units: str = Field(..., description="'metric' or 'imperial'")) -> dict:
    """Set preferred units (metric or imperial)."""
    if units not in ["metric", "imperial"]:
        return {"success": False, "error": "Units must be 'metric' or 'imperial'"}
    config = load_config()
    config["units"] = units
    save_config(config)
    desc = "Celsius, km/h, mm" if units == "metric" else "Fahrenheit, mph, inches"
    return {"success": True, "message": f"Units set to {units} ({desc})"}


@mcp.tool()
async def save_location(
    name: str = Field(..., description="Alias (e.g., 'home', 'work')"),
    city: str = Field(..., description="City name")
) -> dict:
    """Save a location with a custom alias."""
    location = await geocode_city(city)
    config = load_config()
    config.setdefault("saved_locations", {})[name.lower()] = city
    save_config(config)
    return {"success": True, "message": f"Saved '{name}' as {location['name']}, {location['country']}"}


@mcp.tool()
async def list_saved_locations() -> dict:
    """List all saved locations."""
    config = load_config()
    return {
        "default": config.get("default_location"),
        "saved": config.get("saved_locations", {}),
        "units": config.get("units", "metric"),
    }


@mcp.tool()
async def get_celestial_overview(
    city: Optional[str] = Field(None, description="City name (optional if default is set)")
) -> str:
    """
    Get complete overview: weather, moon phase, season, sun times, air quality.
    This is the main tool - use this for all weather/astronomy queries.
    """
    location = await resolve_location(city)
    moon = get_moon_phase_info()
    season = get_season_info(location["latitude"])
    sun = get_sun_times(location["latitude"], location["longitude"], location["timezone"])
    weather = await get_weather(location["latitude"], location["longitude"], location["timezone"])
    air = await get_air_quality(location["latitude"], location["longitude"])
    meteors = get_upcoming_meteor_showers(30)

    current = weather["current"]

    text = f"""Celestial Overview for {location['name']}, {location['country']}
{'=' * 50}

WEATHER
-------
{current['conditions']}
Temperature: {format_temp(current['temperature'])} (feels like {format_temp(current['feels_like'])})
Humidity: {current['humidity']}% | Wind: {format_speed(current['wind_speed'])}
Cloud Cover: {current['cloud_cover']}% | UV Index: {current['uv_index']}

AIR QUALITY
-----------
AQI: {air['us_aqi']} ({air['category']})
PM2.5: {air['pm2_5']} µg/m³

MOON
----
Phase: {moon['phase_name']} ({moon['illumination_percent']}% illuminated)
Next Full Moon: {moon['next_full_moon']}

SEASON
------
{season['current_season']} ({season['hemisphere']} Hemisphere)
{season['next_season']} begins in {season['days_until_next_season']} days ({season['next_season_starts']})

SUN
---"""

    if "status" in sun:
        text += f"\n{sun['status']}"
    else:
        text += f"""
Sunrise: {sun['sunrise']} | Sunset: {sun['sunset']}
Day Length: {sun['day_length']}
Golden Hour Evening: {sun['golden_hour_evening']}"""

    if meteors:
        text += "\n\nUPCOMING METEOR SHOWERS\n----------------------"
        for m in meteors[:3]:
            text += f"\n{m['name']}: {m['peak_date']} ({m['days_until']} days) - {m['expected_rate']}"

    # 7-day forecast
    text += "\n\n7-DAY FORECAST\n--------------"
    for day in weather["forecast"]:
        text += f"\n{day['date']}: {day['conditions']} | High: {format_temp(day['high'])} Low: {format_temp(day['low'])} | Rain: {day['precipitation_chance']}%"

    return text


# =============================================================================
# MAIN
# =============================================================================

def main():
    mcp.run()

if __name__ == "__main__":
    main()
