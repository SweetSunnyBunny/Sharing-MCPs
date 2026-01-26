# Tumblr MCP Server

Let Claude post to your Tumblr blog! This MCP (Model Context Protocol) server gives Claude the ability to:

- Create text, photo, quote, and link posts
- Reblog posts from other blogs
- View your dashboard
- Follow blogs and search tags
- Manage your posts

## Quick Start

### 1. Create a Tumblr App

1. Go to https://www.tumblr.com/oauth/apps
2. Click "Register application"
3. Fill in:
   - **Application name:** My Tumblr Bot
   - **Description:** Personal posting bot
   - **Default callback URL:** `http://localhost:9876/callback`
   - **OAuth2 redirect URLs:** `http://localhost:9876/callback`
4. Click Register
5. Note your **OAuth Consumer Key** and **OAuth Consumer Secret**

### 2. Install & Authenticate

```bash
# Install dependencies
pip install -r requirements.txt

# Run setup (will open browser to authenticate)
python setup.py

# Check status
python setup.py --status
```

### 3. Run the Server

```bash
python run_server.py
```

The server runs on `http://localhost:8080/mcp`

---

## Deployment Options

### Option A: Self-Hosted with Cloudflare Tunnel (Recommended - $5/year)

This is the best option for always-on access from anywhere (phone, other computers, etc.)

#### What You Need
- A computer that stays on (or a Raspberry Pi, old laptop, etc.)
- A domain name (~$5-10/year from Cloudflare, Namecheap, etc.)
- Free Cloudflare account

#### Step 1: Get a Domain

1. Buy a cheap domain (I recommend Cloudflare Registrar - no markup, ~$5-10/year for .uk, .xyz, etc.)
2. Or use any domain and point its nameservers to Cloudflare

#### Step 2: Set Up Cloudflare Tunnel

1. Create free account at https://dash.cloudflare.com
2. Add your domain to Cloudflare
3. Install cloudflared:
   - **Windows:** Download from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
   - **Mac:** `brew install cloudflared`
   - **Linux:** See Cloudflare docs

4. Login and create tunnel:
```bash
cloudflared tunnel login
cloudflared tunnel create my-tunnel
```

5. Create config file at `~/.cloudflared/config.yml`:
```yaml
tunnel: YOUR-TUNNEL-ID
credentials-file: ~/.cloudflared/YOUR-TUNNEL-ID.json

ingress:
  - hostname: tumblr.yourdomain.com
    service: http://localhost:8080
  - service: http_status:404
```

6. Route DNS:
```bash
cloudflared tunnel route dns my-tunnel tumblr.yourdomain.com
```

7. Run tunnel:
```bash
cloudflared tunnel run my-tunnel
```

#### Step 3: Run the MCP Server

```bash
python run_server.py
```

Now your MCP is available at `https://tumblr.yourdomain.com/mcp` from anywhere!

#### Make It Start Automatically

**Windows:** Create a batch file and add to Startup folder
**Mac/Linux:** Use systemd or launchd

---

### Option B: Cloud Deployment (Railway/Render)

Deploy to the cloud if you don't want to run your own server.

#### Railway (Easy, free tier available)

1. Push this code to a GitHub repository
2. Go to https://railway.app
3. Click "New Project" → "Deploy from GitHub repo"
4. Select your repo
5. Railway will auto-detect and deploy

After deployment:
1. Go to your Railway project settings
2. Find your public URL (something like `your-app.up.railway.app`)
3. Your MCP endpoint is at `https://your-app.up.railway.app/mcp`

**Important:** You'll need to set up credentials. Either:
- SSH into the Railway container and run `python setup.py`
- Or manually create `config/credentials.json` with your tokens

#### Render (Similar to Railway)

1. Push to GitHub
2. Go to https://render.com
3. New → Web Service → Connect your repo
4. Set start command: `python run_server.py`
5. Deploy

---

### Option C: Docker

```bash
# Build
docker build -t tumblr-mcp .

# Run (mount config for persistence)
docker run -p 8080:8080 -v $(pwd)/config:/app/config tumblr-mcp
```

---

## Connecting to Claude

### Claude Code (CLI)

Add to your Claude Code MCP settings:

```json
{
  "mcpServers": {
    "tumblr": {
      "url": "https://tumblr.yourdomain.com/mcp"
    }
  }
}
```

### Other MCP Clients

Use the URL format: `https://your-server/mcp`

---

## Available Tools

| Tool | Description |
|------|-------------|
| `tumblr_test_connection` | Test connection and show account info |
| `tumblr_get_user_info` | Get detailed account info |
| `tumblr_create_text_post` | Create a text post |
| `tumblr_create_photo_post` | Create a photo post from URL |
| `tumblr_create_quote_post` | Create a quote post |
| `tumblr_create_link_post` | Create a link post |
| `tumblr_reblog` | Reblog a post |
| `tumblr_get_posts` | Get your posts |
| `tumblr_delete_post` | Delete a post |
| `tumblr_get_dashboard` | View your dashboard |
| `tumblr_follow` | Follow a blog |
| `tumblr_search_tag` | Search posts by tag |

---

## Troubleshooting

### "Not configured" error
Run `python setup.py` to authenticate.

### Token expired
The server automatically refreshes tokens. If it fails, run `python setup.py` again.

### Can't connect remotely
Make sure your tunnel is running and the domain is correctly configured.

---

## Security Notes

- Your credentials are stored in `config/credentials.json` - keep this private!
- The `.gitignore` in the config folder prevents accidental commits
- If using cloud deployment, consider using environment variables for secrets

---

## License

MIT - Do whatever you want with it!

Built with love for sharing.
