# World Tools MCP Server

Give Claude awareness of the real world - weather, time, moon phases, and the ability to read web pages and view images from URLs.

This MCP (Model Context Protocol) server provides:

- Current weather and 3-day forecast for any location (via Open-Meteo, no API key needed)
- "Home weather" with configurable coordinates for quick checks
- Date/time with timezone support
- Moon phase calculations
- Web page text extraction (with SSRF protection)
- Image viewing from URLs

## Why This is Awesome

Claude doesn't know what the weather is, what day it is, or what's on a web page. This MCP fixes all of that with zero API keys required. Weather data comes from Open-Meteo (free, no signup), and the web tools let Claude read pages and view images from any public URL.

**Use cases:**
- "What's the weather like?" - instant answer
- "What day of the week is my birthday this year?" - calendar lookups
- "Read this article for me" - extract text from any URL
- "Show me this image" - view images from URLs
- Moon phase tracking for the curious

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Home Location (optional)

Set environment variables for the "home weather" shortcut:

```bash
export WT_HOME_LAT=51.5074    # Your latitude
export WT_HOME_LON=-0.1278    # Your longitude
export WT_HOME_LABEL="London" # Display name
```

On Windows:
```cmd
set WT_HOME_LAT=51.5074
set WT_HOME_LON=-0.1278
set WT_HOME_LABEL=London
```

### 3. Run Locally (stdio, for Claude Code)

```bash
python run_server.py
```

### 4. Run as HTTP Server (for remote access)

```bash
python run_server.py --transport streamable-http --port 8091
```

The server runs on `http://localhost:8091/mcp`

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `WT_HOME_LAT` | `0.0` | Home latitude for quick weather checks |
| `WT_HOME_LON` | `0.0` | Home longitude for quick weather checks |
| `WT_HOME_LABEL` | `Home` | Display name for home location |

---

## Cloudflare Tunnel Setup (~$5/year)

This is how you make your MCP accessible from anywhere. Cloudflare Tunnels are free - you just need a domain (~$5-10/year).

### What You'll Get
- Check weather and read web pages from your phone
- Secure HTTPS connection
- No port forwarding needed

### Step 1: Get a Domain

1. Go to https://www.cloudflare.com/products/registrar/
2. Search for a cheap domain (.uk, .xyz, .site are often ~$5)
3. Buy it through Cloudflare (no markup, includes free DNS)

Or use any domain you already own and point its nameservers to Cloudflare.

### Step 2: Install Cloudflared

**Windows:**
1. Download from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
2. Extract to a folder like `C:\Program Files\cloudflared\`
3. Add to PATH or use full path

**Mac:**
```bash
brew install cloudflared
```

**Linux:**
```bash
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
```

### Step 3: Login, Create Tunnel, Configure

```bash
cloudflared tunnel login
cloudflared tunnel create my-mcp-tunnel
```

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: YOUR-TUNNEL-ID-HERE
credentials-file: /path/to/.cloudflared/YOUR-TUNNEL-ID.json

ingress:
  - hostname: tools.yourdomain.com
    service: http://localhost:8091
  - service: http_status:404
```

### Step 4: Route DNS and Run

```bash
cloudflared tunnel route dns my-mcp-tunnel tools.yourdomain.com
```

**Terminal 1 - Start the MCP server:**
```bash
python run_server.py --transport streamable-http --port 8091
```

**Terminal 2 - Start the tunnel:**
```bash
cloudflared tunnel run my-mcp-tunnel
```

Your MCP is now available at `https://tools.yourdomain.com/mcp`!

---

## Connecting to Claude

### Claude Code (CLI) - Local (stdio)

```json
{
  "mcpServers": {
    "world-tools": {
      "command": "python",
      "args": ["path/to/world-tools-mcp/run_server.py"],
      "env": {
        "WT_HOME_LAT": "51.5074",
        "WT_HOME_LON": "-0.1278",
        "WT_HOME_LABEL": "London"
      }
    }
  }
}
```

### Claude Code (CLI) - Remote (via tunnel)

```json
{
  "mcpServers": {
    "world-tools": {
      "url": "https://tools.yourdomain.com/mcp"
    }
  }
}
```

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

## Security Notes

- Web tools block requests to localhost and private/internal IPs (SSRF protection)
- Only HTTP/HTTPS URLs are allowed
- Response size limits prevent memory exhaustion (5MB HTML, 20MB images)
- Images are cached in the system temp directory and auto-cleaned after 24 hours
- No API keys required - weather data comes from Open-Meteo (free)

---

## Troubleshooting

### Weather returns wrong location
The geocoder picks the top result. Try being more specific (e.g., "Portland, Oregon" instead of "Portland").

### Web page text looks garbled
The text extractor strips HTML tags. Some JavaScript-heavy sites may not return useful content since this doesn't execute JS.

### "URL resolves to a local/private network address"
This is the SSRF protection working as intended. The web tools only fetch from public internet addresses.

### Home weather not working
Make sure you've set `WT_HOME_LAT` and `WT_HOME_LON` environment variables. Without them, it defaults to 0,0 (middle of the ocean).

---

## License

MIT - Do whatever you want with it!
