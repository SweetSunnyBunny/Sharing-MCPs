# Books MCP Server

Read EPUB books with Claude! Track your progress, add bookmarks and notes, and pick up right where you left off.

This MCP (Model Context Protocol) server gives Claude the ability to:

- Read EPUB books chapter by chapter
- Track reading progress across sessions
- Bookmark chapters and add reading notes
- Search within books for specific passages
- Break chapters into sections for discussion

## Why This is Awesome

Ever wanted to read a book *with* Claude? Now you can! Drop EPUBs into the library folder, and Claude can read them, discuss them, and remember where you left off. Works from your phone too with a Cloudflare Tunnel.

**Use cases:**
- Read books together and discuss as you go
- Have Claude summarize or analyze chapters
- Search for quotes and passages
- Keep reading notes and bookmarks

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Add Books

Drop your `.epub` files into the `library/` folder (created automatically on first run).

### 3. Run Locally (stdio, for Claude Code)

```bash
python run_server.py
```

### 4. Run as HTTP Server (for remote access)

```bash
python run_server.py --transport streamable-http --port 8770
```

The server runs on `http://localhost:8770/mcp`

---

## Cloudflare Tunnel Setup (~$5/year)

This is how you make your MCP accessible from anywhere. Cloudflare Tunnels are free - you just need a domain (~$5-10/year).

### What You'll Get
- Read books on your computer from your phone
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
  - hostname: books.yourdomain.com
    service: http://localhost:8770
  - service: http_status:404
```

### Step 4: Route DNS and Run

```bash
cloudflared tunnel route dns my-mcp-tunnel books.yourdomain.com
```

**Terminal 1 - Start the MCP server:**
```bash
python run_server.py --transport streamable-http --port 8770
```

**Terminal 2 - Start the tunnel:**
```bash
cloudflared tunnel run my-mcp-tunnel
```

Your MCP is now available at `https://books.yourdomain.com/mcp`!

---

## Connecting to Claude

### Claude Code (CLI) - Local (stdio)

```json
{
  "mcpServers": {
    "books": {
      "command": "python",
      "args": ["path/to/books-mcp/run_server.py"]
    }
  }
}
```

### Claude Code (CLI) - Remote (via tunnel)

```json
{
  "mcpServers": {
    "books": {
      "url": "https://books.yourdomain.com/mcp"
    }
  }
}
```

---

## Available Tools

| Tool | Description |
|------|-------------|
| `list_books` | List all EPUBs in the library |
| `get_book_info` | Get metadata, TOC, and progress for a book |
| `read_chapter` | Read a chapter (auto-continues from last position) |
| `search_book` | Search within a book for text |
| `add_bookmark` | Bookmark a chapter with an optional note |
| `add_reading_note` | Save thoughts or observations about a chapter |
| `get_reading_notes` | Review all your reading notes |
| `summarize_chapter` | Break a chapter into sections for discussion |

---

## Data Storage

Reading progress, bookmarks, and notes are stored as JSON files alongside the server:

- `reading_progress.json` - Which chapter you're on per book
- `bookmarks.json` - Your bookmarked chapters
- `reading_notes.json` - Your notes and observations

These persist across sessions so you always pick up where you left off.

---

## Security Notes

- This server reads EPUB files from the `library/` directory only
- No file write access outside of the JSON data files
- If exposing remotely, use a Cloudflare Tunnel for encryption

---

## Troubleshooting

### "Book not found"
Make sure the `.epub` file is in the `library/` folder. The server searches by filename or auto-generated ID.

### Chapter content looks garbled
Some EPUBs use unusual HTML structures. The server strips HTML to plain text, which works well for most books but may lose some formatting.

### Progress not saving
Check that the server has write permissions in its own directory for the JSON files.

---

## License

MIT - Do whatever you want with it!
