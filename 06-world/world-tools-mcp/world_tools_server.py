"""
World Tools MCP server.

Tools included:
- Weather (current + forecast) via Open-Meteo
- Clock/calendar with moon phase
- Web page text reading
- Image viewing from URL
"""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import re
import socket
import tempfile
from datetime import date, datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as url_error
from urllib import parse as url_parse
from urllib import request as url_request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from pydantic import Field

mcp = FastMCP("world-tools")

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
REQUEST_TIMEOUT = 25
IMAGE_CACHE_DIR = Path(tempfile.gettempdir()) / "world-tools-mcp-images"

# Set your home coordinates via environment variables for the "home weather" tool.
# Defaults to 0,0 (null island) if not configured.
HOME_LAT = float(os.environ.get("WT_HOME_LAT", "0.0"))
HOME_LON = float(os.environ.get("WT_HOME_LON", "0.0"))
HOME_LABEL = os.environ.get("WT_HOME_LABEL", "Home")

WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object response")
    return data


def _http_request(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = REQUEST_TIMEOUT,
    max_bytes: int = MAX_HTML_BYTES,
) -> Dict[str, Any]:
    req = url_request.Request(url=url, headers=headers or {}, method="GET")
    try:
        with url_request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "")
            chunks: List[bytes] = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"Response too large (> {max_bytes} bytes)")
                chunks.append(chunk)
            return {
                "status": resp.getcode(),
                "content_type": content_type,
                "final_url": resp.geturl(),
                "data": b"".join(chunks),
            }
    except url_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}")
    except url_error.URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}")


def _is_blocked_ip(host_ip: str) -> bool:
    ip = ip_address(host_ip)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_public_http_url(url: str) -> url_parse.SplitResult:
    parsed = url_parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http/https URLs are allowed.")
    if not parsed.netloc:
        raise ValueError("URL must include a host.")

    host = (parsed.hostname or "").strip().lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Localhost URLs are not allowed.")

    try:
        addr_info = socket.getaddrinfo(host, parsed.port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host '{host}': {exc}")

    seen_ips = set()
    for info in addr_info:
        ip = info[4][0]
        if ip in seen_ips:
            continue
        seen_ips.add(ip)
        if _is_blocked_ip(ip):
            raise ValueError("URL resolves to a local/private network address, which is blocked.")

    return parsed


def _extract_title(html_text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()


def _extract_meta_description(html_text: str) -> str:
    # name=description
    match = re.search(
        r"<meta[^>]+name=['\"]description['\"][^>]+content=['\"](.*?)['\"]",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()

    # property=og:description
    match = re.search(
        r"<meta[^>]+property=['\"]og:description['\"][^>]+content=['\"](.*?)['\"]",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()

    return ""


def _extract_text_from_html(html_text: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html_text, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<noscript\b[^<]*(?:(?!</noscript>)<[^<]*)*</noscript>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_links(html_text: str, base_url: str, limit: int = 30) -> List[str]:
    matches = re.findall(r"<a[^>]+href=['\"](.*?)['\"]", html_text, flags=re.IGNORECASE)
    links: List[str] = []
    for href in matches:
        if href.startswith("#"):
            continue
        absolute = url_parse.urljoin(base_url, href)
        try:
            parsed = url_parse.urlsplit(absolute)
        except Exception:
            continue
        if parsed.scheme not in {"http", "https"}:
            continue
        links.append(absolute)
        if len(links) >= limit:
            break
    return links


def _get_tz(tz_name: str) -> timezone:
    if tz_name.lower() in {"local", "system"}:
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        raise ValueError(f"Unknown timezone: {tz_name}")


def _moon_phase_fraction(target: date) -> float:
    # Reference new moon: 2000-01-06 18:14 UTC
    reference = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    target_dt = datetime(target.year, target.month, target.day, 12, 0, tzinfo=timezone.utc)
    days_since = (target_dt - reference).total_seconds() / 86400.0
    synodic_month = 29.53058867
    return (days_since % synodic_month) / synodic_month


def _moon_phase_name(phase: float) -> str:
    boundaries: List[Tuple[float, str]] = [
        (0.0625, "New Moon"),
        (0.1875, "Waxing Crescent"),
        (0.3125, "First Quarter"),
        (0.4375, "Waxing Gibbous"),
        (0.5625, "Full Moon"),
        (0.6875, "Waning Gibbous"),
        (0.8125, "Last Quarter"),
        (0.9375, "Waning Crescent"),
        (1.0, "New Moon"),
    ]
    for threshold, name in boundaries:
        if phase < threshold:
            return name
    return "New Moon"


def _is_full_moon_tonight(target: date) -> bool:
    phase = _moon_phase_fraction(target)
    return abs(phase - 0.5) <= 0.03


def _weather_description(code: Optional[int]) -> str:
    if code is None:
        return "Unknown"
    return WMO_WEATHER_CODES.get(code, f"Unknown weather code {code}")


def _cleanup_old_images(max_age_hours: int = 24) -> None:
    if not IMAGE_CACHE_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    for item in IMAGE_CACHE_DIR.glob("*"):
        try:
            if not item.is_file():
                continue
            modified = datetime.fromtimestamp(item.stat().st_mtime)
            if modified < cutoff:
                item.unlink(missing_ok=True)
        except Exception:
            continue


@mcp.tool(name="wt_time_now")
async def time_now(
    timezone_name: str = Field("local", description="IANA timezone like 'America/New_York' or 'local'"),
) -> Dict[str, Any]:
    """Get current date/time info in a timezone."""
    try:
        tz = _get_tz(timezone_name)
        now = datetime.now(tz)
        today = now.date()
        phase = _moon_phase_fraction(today)
        return {
            "success": True,
            "timezone": str(now.tzinfo),
            "iso": now.isoformat(),
            "date": today.isoformat(),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
            "is_weekend": now.weekday() >= 5,
            "moon_phase": _moon_phase_name(phase),
            "full_moon_tonight": _is_full_moon_tonight(today),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool(name="wt_calendar_info")
async def calendar_info(
    date_iso: Optional[str] = Field(None, description="Date as YYYY-MM-DD. Defaults to today in selected timezone."),
    timezone_name: str = Field("local", description="IANA timezone like 'America/New_York' or 'local'"),
) -> Dict[str, Any]:
    """Get day-of-week and moon info for a date."""
    try:
        tz = _get_tz(timezone_name)
        if date_iso:
            target = date.fromisoformat(date_iso)
        else:
            target = datetime.now(tz).date()

        tomorrow = target + timedelta(days=1)
        phase = _moon_phase_fraction(target)
        return {
            "success": True,
            "timezone": str(tz),
            "date": target.isoformat(),
            "weekday": target.strftime("%A"),
            "is_weekend": target.weekday() >= 5,
            "tomorrow": tomorrow.isoformat(),
            "tomorrow_weekday": tomorrow.strftime("%A"),
            "moon_phase": _moon_phase_name(phase),
            "full_moon_tonight": _is_full_moon_tonight(target),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _weather_for_coordinates(
    latitude: float,
    longitude: float,
    location_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Fetch weather payload for known coordinates."""
    forecast_url = (
        "https://api.open-meteo.com/v1/forecast?"
        + url_parse.urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "timezone": "auto",
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
                "forecast_days": 3,
            }
        )
    )
    wx_resp = _http_request(forecast_url, max_bytes=1024 * 1024)
    wx = _safe_json_loads(wx_resp["data"].decode("utf-8", errors="replace"))

    current = wx.get("current", {})
    daily = wx.get("daily", {})
    daily_time = daily.get("time", [])
    daily_codes = daily.get("weather_code", [])
    daily_max = daily.get("temperature_2m_max", [])
    daily_min = daily.get("temperature_2m_min", [])

    forecast = []
    for i, day in enumerate(daily_time[:3]):
        code = daily_codes[i] if i < len(daily_codes) else None
        hi = daily_max[i] if i < len(daily_max) else None
        lo = daily_min[i] if i < len(daily_min) else None
        forecast.append(
            {
                "date": day,
                "weather_code": code,
                "weather": _weather_description(code),
                "temp_max_c": hi,
                "temp_min_c": lo,
            }
        )

    code = current.get("weather_code")
    merged_location = {
        "name": location_info.get("name"),
        "admin1": location_info.get("admin1"),
        "country": location_info.get("country"),
        "latitude": latitude,
        "longitude": longitude,
        "timezone": wx.get("timezone"),
    }
    if location_info.get("source"):
        merged_location["source"] = location_info["source"]

    return {
        "success": True,
        "location": merged_location,
        "current": {
            "time": current.get("time"),
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_percent": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "is_day": bool(current.get("is_day", 0)),
            "weather_code": code,
            "weather": _weather_description(code),
        },
        "forecast_3d": forecast,
        "sunrise": daily.get("sunrise", [None])[0],
        "sunset": daily.get("sunset", [None])[0],
        "source": "Open-Meteo",
    }


@mcp.tool(name="wt_weather_current")
async def weather_current(
    location: Optional[str] = Field(
        None,
        description="City or location name. If omitted, uses configured home coordinates.",
    ),
) -> Dict[str, Any]:
    """Get current weather and a short forecast using Open-Meteo."""
    try:
        if not location or not location.strip():
            return _weather_for_coordinates(
                HOME_LAT,
                HOME_LON,
                {
                    "name": HOME_LABEL,
                    "admin1": None,
                    "country": None,
                    "source": "configured_home_coordinates",
                },
            )

        geocode_url = (
            "https://geocoding-api.open-meteo.com/v1/search?"
            + url_parse.urlencode({"name": location, "count": 1, "language": "en", "format": "json"})
        )
        geo_resp = _http_request(geocode_url, max_bytes=512000)
        geo = _safe_json_loads(geo_resp["data"].decode("utf-8", errors="replace"))
        results = geo.get("results") or []
        if not results:
            return {"success": False, "error": f"Location not found: {location}"}

        place = results[0]
        lat = place.get("latitude")
        lon = place.get("longitude")
        if lat is None or lon is None:
            return {"success": False, "error": "Geocoding returned invalid coordinates."}

        return _weather_for_coordinates(
            float(lat),
            float(lon),
            {
                "name": place.get("name"),
                "admin1": place.get("admin1"),
                "country": place.get("country"),
                "source": "geocoded_location",
            },
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool(name="wt_weather_home")
async def weather_home() -> Dict[str, Any]:
    """Get weather using configured home coordinates (set via WT_HOME_LAT / WT_HOME_LON env vars)."""
    try:
        return _weather_for_coordinates(
            HOME_LAT,
            HOME_LON,
            {
                "name": HOME_LABEL,
                "admin1": None,
                "country": None,
                "source": "configured_home_coordinates",
            },
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool(name="wt_web_read_url")
async def web_read_url(
    url: str = Field(..., description="Public web URL to read"),
    max_chars: int = Field(12000, description="Max characters of extracted text"),
    include_links: bool = Field(True, description="Include discovered page links"),
) -> Dict[str, Any]:
    """Fetch a public URL and extract readable text."""
    try:
        _validate_public_http_url(url)
        resp = _http_request(
            url,
            headers={"User-Agent": "world-tools-mcp/1.0"},
            max_bytes=MAX_HTML_BYTES,
        )

        content_type = (resp.get("content_type") or "").lower()
        raw = resp["data"]
        final_url = resp.get("final_url", url)

        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            text = raw.decode("utf-8", errors="replace")
            snippet = text[: max(1000, min(max_chars, 40000))]
            return {
                "success": True,
                "url": final_url,
                "content_type": content_type,
                "title": "",
                "description": "",
                "text": snippet,
                "text_length": len(snippet),
                "links": [],
            }

        html_text = raw.decode("utf-8", errors="replace")
        title = _extract_title(html_text)
        description = _extract_meta_description(html_text)
        extracted_text = _extract_text_from_html(html_text)
        clipped = extracted_text[: max(1000, min(max_chars, 80000))]

        links = _extract_links(html_text, final_url, limit=40) if include_links else []

        return {
            "success": True,
            "url": final_url,
            "content_type": content_type,
            "title": title,
            "description": description,
            "text": clipped,
            "text_length": len(clipped),
            "links": links,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool(name="wt_web_view_image_url")
async def web_view_image_url(
    url: str = Field(..., description="Public image URL (http/https)"),
):
    """Download an image URL and return it as an MCP image for visual viewing."""
    try:
        _validate_public_http_url(url)
        _cleanup_old_images()

        resp = _http_request(
            url,
            headers={"User-Agent": "world-tools-mcp/1.0"},
            max_bytes=MAX_IMAGE_BYTES,
        )

        content_type = (resp.get("content_type") or "").lower()
        data = resp["data"]
        final_url = resp.get("final_url", url)

        if not content_type.startswith("image/"):
            guessed, _ = mimetypes.guess_type(url_parse.urlsplit(final_url).path)
            if not guessed or not guessed.startswith("image/"):
                return {
                    "success": False,
                    "error": f"URL did not return an image content type: {content_type or 'unknown'}",
                }
            content_type = guessed

        IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(final_url.encode("utf-8")).hexdigest()[:24]
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".img"
        image_path = IMAGE_CACHE_DIR / f"{digest}{ext}"
        image_path.write_bytes(data)

        return Image(path=str(image_path.resolve()))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
