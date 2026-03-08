# Terminal MCP Server

Give Claude persistent terminal sessions that remember state between commands!

This MCP (Model Context Protocol) server provides:

- Persistent shell sessions (cwd, env vars, aliases survive across commands)
- Multiple concurrent sessions
- Auto-creation of default sessions
- Configurable timeouts and output limits
- Automatic shell respawning if a session dies

## Why This is Awesome

Normal MCP terminal tools start a fresh shell for every command. That means `cd`, `export`, `alias`, and other stateful operations are lost immediately. This server keeps shell sessions alive, so state persists naturally - just like a real terminal.

**Use cases:**
- Run multi-step build/deploy workflows where each step depends on the previous
- Set up environment variables once and use them across commands
- Navigate directories without losing your place
- Run long-lived processes and check on them later

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Locally (stdio, for Claude Code)

```bash
python run_server.py
```

### 3. Run as HTTP Server (for remote access)

```bash
python run_server.py --transport streamable-http --host 127.0.0.1 --port 8793
```

The server runs on `http://localhost:8793/mcp`

---

## Configuration

All settings are configurable via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TERMINAL_SHELL` | `bash` | Shell to use for new sessions |
| `TERMINAL_DEFAULT_CWD` | Home directory | Default working directory |
| `TERMINAL_TIMEOUT` | `120` | Command timeout in seconds |
| `TERMINAL_MAX_OUTPUT` | `100000` | Max output characters before truncation |
| `TERMINAL_MAX_SESSIONS` | `10` | Maximum concurrent sessions |

---

## Cloudflare Tunnel Setup (~$5/year)

This is how you make your MCP accessible from anywhere. Cloudflare Tunnels are free - you just need a domain (~$5-10/year).

### What You'll Get
- Run terminal commands on your computer from your phone
- Secure HTTPS connection
- No port forwarding needed
- Works even behind firewalls

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
# Debian/Ubuntu
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
  - hostname: terminal.yourdomain.com
    service: http://localhost:8793
  - service: http_status:404
```

### Step 4: Route DNS and Run

```bash
cloudflared tunnel route dns my-mcp-tunnel terminal.yourdomain.com
```

**Terminal 1 - Start the MCP server:**
```bash
python run_server.py --transport streamable-http --port 8793
```

**Terminal 2 - Start the tunnel:**
```bash
cloudflared tunnel run my-mcp-tunnel
```

Your MCP is now available at `https://terminal.yourdomain.com/mcp`!

---

## Connecting to Claude

### Claude Code (CLI) - Local (stdio)

```json
{
  "mcpServers": {
    "terminal": {
      "command": "python",
      "args": ["path/to/terminal-mcp/run_server.py"]
    }
  }
}
```

### Claude Code (CLI) - Remote (via tunnel)

```json
{
  "mcpServers": {
    "terminal": {
      "url": "https://terminal.yourdomain.com/mcp"
    }
  }
}
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `terminal_execute` | Run a command in a persistent session |
| `terminal_create` | Create a new named session |
| `terminal_list` | List all active sessions |
| `terminal_destroy` | Kill a session and its shell process |
| `terminal_get_info` | Get detailed info about a session |

---

## Security Notes

- This server gives shell access to your machine - only expose it via your private tunnel
- The tunnel is encrypted (HTTPS) and tied to your Cloudflare account
- Consider running with a restricted user account if exposing remotely
- Don't share your tunnel credentials

---

## Troubleshooting

### "Connection refused"
Make sure the MCP server is running before the tunnel.

### "Bad gateway"
Check that the port in config.yml matches the server port (default: 8793).

### Shell dies between commands
The server automatically respawns dead shells. If it keeps happening, check your shell path with `TERMINAL_SHELL`.

### Output looks garbled
The server filters shell noise (prompts, echoed commands), but some shells may need tweaking. Stick with `bash` for best results.

---

## License

MIT - Do whatever you want with it!
