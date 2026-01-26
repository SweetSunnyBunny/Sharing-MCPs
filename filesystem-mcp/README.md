# Filesystem MCP Server

Let Claude access files on your computer from anywhere - your phone, other computers, etc.!

This MCP (Model Context Protocol) server gives Claude the ability to:

- List directories and browse your files
- Read text files and view images
- Write and edit files
- Copy, move, and delete files
- Search for files by name or content
- See recently modified files

## Why This is Awesome

Ever use Claude from your phone and wish it could see files on your computer? Now it can! With this MCP and a Cloudflare Tunnel (costs ~$5/year for a domain), Claude can access your files from anywhere.

**Use cases:**
- View images Claude saved to disk (from Discord, etc.)
- Read log files when debugging remotely
- Access your code from your phone
- Let Claude help organize your files

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Run Locally (for testing)

```bash
python run_server.py
```

The server runs on `http://localhost:8080/mcp`

### 3. Set Up Remote Access (the fun part!)

See the **Cloudflare Tunnel Setup** section below.

---

## Cloudflare Tunnel Setup (~$5/year)

This is how you make your MCP accessible from anywhere. Cloudflare Tunnels are free - you just need a domain (~$5-10/year).

### What You'll Get
- Access your computer's files from your phone
- Secure HTTPS connection
- No port forwarding needed
- Works even behind firewalls

### Cost Breakdown
- **Domain:** ~$5-10/year (Cloudflare, Namecheap, etc.)
- **Cloudflare account:** Free
- **Cloudflare Tunnel:** Free

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

# Or use the package manager for your distro
```

### Step 3: Login to Cloudflare

```bash
cloudflared tunnel login
```

This opens a browser to authenticate. Select your domain.

### Step 4: Create a Tunnel

```bash
cloudflared tunnel create my-mcp-tunnel
```

Note the tunnel ID it gives you (looks like: `a1b2c3d4-e5f6-7890-abcd-ef1234567890`)

### Step 5: Configure the Tunnel

Create a config file at `~/.cloudflared/config.yml` (or `C:\Users\YourName\.cloudflared\config.yml` on Windows):

```yaml
tunnel: YOUR-TUNNEL-ID-HERE
credentials-file: /path/to/.cloudflared/YOUR-TUNNEL-ID.json

ingress:
  # Filesystem MCP
  - hostname: files.yourdomain.com
    service: http://localhost:8080

  # Catch-all (required)
  - service: http_status:404
```

Replace:
- `YOUR-TUNNEL-ID-HERE` with your tunnel ID
- `/path/to/.cloudflared/` with your actual path
- `files.yourdomain.com` with your subdomain

### Step 6: Route DNS

```bash
cloudflared tunnel route dns my-mcp-tunnel files.yourdomain.com
```

### Step 7: Run Everything

**Terminal 1 - Start the MCP server:**
```bash
python run_server.py
```

**Terminal 2 - Start the tunnel:**
```bash
cloudflared tunnel run my-mcp-tunnel
```

Your MCP is now available at `https://files.yourdomain.com/mcp`!

---

## Running on Startup

### Windows

Create `start-mcp.bat`:
```batch
@echo off
echo Starting Filesystem MCP...
start "Filesystem MCP" /min cmd /c "cd /d C:\path\to\filesystem-mcp && python run_server.py"
timeout /t 3 /nobreak > nul

echo Starting Cloudflare Tunnel...
"C:\Program Files\cloudflared\cloudflared.exe" tunnel run my-mcp-tunnel
```

Add a shortcut to this batch file in your Startup folder.

### Mac/Linux

Create a systemd service or launchd plist. Example systemd service:

```ini
# /etc/systemd/system/filesystem-mcp.service
[Unit]
Description=Filesystem MCP Server
After=network.target

[Service]
Type=simple
User=yourusername
WorkingDirectory=/path/to/filesystem-mcp
ExecStart=/usr/bin/python3 run_server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## Connecting to Claude

### Claude Code (CLI)

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "filesystem": {
      "url": "https://files.yourdomain.com/mcp"
    }
  }
}
```

### Other MCP Clients

Use the URL: `https://files.yourdomain.com/mcp`

---

## Available Tools

| Tool | Description |
|------|-------------|
| `fs_list_directory` | List folder contents |
| `fs_create_directory` | Create new folders |
| `fs_read_file` | Read text files |
| `fs_read_image` | View images visually |
| `fs_get_file_info` | Get file metadata |
| `fs_write_file` | Write/append to files |
| `fs_write_binary` | Write binary files |
| `fs_copy` | Copy files/folders |
| `fs_move` | Move/rename files |
| `fs_delete` | Delete files/folders |
| `fs_search` | Find files by pattern |
| `fs_search_content` | Search text in files |
| `fs_list_drives` | List drives (Windows) |
| `fs_get_recent_files` | Find recently modified files |

---

## Adding More MCPs

The tunnel system can host multiple MCPs! Just add more entries to your `config.yml`:

```yaml
ingress:
  - hostname: files.yourdomain.com
    service: http://localhost:8080
  - hostname: tumblr.yourdomain.com
    service: http://localhost:8081
  - hostname: discord.yourdomain.com
    service: http://localhost:8082
  - service: http_status:404
```

Each MCP runs on a different port, and the tunnel routes to the right one based on the hostname.

---

## Security Notes

- This server gives full access to your files - only expose it via your private tunnel
- The tunnel is encrypted (HTTPS) and tied to your Cloudflare account
- Consider which directories you want to access (you can restrict paths if needed)
- Don't share your tunnel credentials

---

## Troubleshooting

### "Connection refused"
Make sure the MCP server is running before the tunnel.

### "Bad gateway"
Check that the port in config.yml matches the server port.

### Images not displaying
Make sure you're using `fs_read_image` or `fs_read_file` on image files.

### Tunnel won't start
Run `cloudflared tunnel login` again to refresh credentials.

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
