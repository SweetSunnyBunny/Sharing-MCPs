# Tumblr MCP

Read your Tumblr dashboard and blogs, manage posts, reblog, and follow blogs from an MCP client. You need a Tumblr account and your own registered Tumblr application. The recommended setup below runs locally on Windows; the Python server can also run on other desktop operating systems with adjusted paths.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\tumblr-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\tumblr-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Register your application and sign in

1. Sign into [Tumblr's application page](https://www.tumblr.com/oauth/apps) and register an application. Give it your own name and description.
2. Set both the default callback URL and OAuth 2 redirect URL to **`http://localhost:9876/callback`**. The address must match exactly; this setup script uses `localhost`, not `127.0.0.1`.
3. Keep the application's OAuth Consumer Key and OAuth Consumer Secret available. These are entered locally during setup; do not paste them into your AI conversation.
4. In the same PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe .\setup.py
```

5. Paste the key and secret when asked. Your browser opens Tumblr's approval page. Sign into the blog owner account and approve the requested access. If a browser does not open, use the URL printed by setup. Return to PowerShell before its two-minute timeout. If asked for a blog name, enter your own primary blog name.
6. Success is reported as authentication for your blog. Setup creates **`config/credentials.json`**, containing your access and refresh tokens. Keep this generated file private. There is no `.env` file to create for this package.

You can inspect the locally saved login with:

```powershell
.\.venv\Scripts\python.exe .\setup.py --status
```

That command reads the saved file; the client tool check below verifies the live connection. For API and application details, see [Tumblr's API documentation](https://github.com/tumblr/docs/blob/master/api.md).

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
    "tumblr-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/tumblr-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/tumblr-mcp/server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask your client: **"Use tumblr_test_connection and tell me which blog is connected."** A successful result identifies your own account/blog. Start with this read-only check before trying a post.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.

- **Redirect mismatch:** use `http://localhost:9876/callback` exactly in the app settings. Close another setup process if port 9876 is already in use, then rerun setup.
- **Missing credentials or expired/revoked login:** run `setup.py` again using this package's virtual environment. Do not copy someone else's credentials file.
- **Blog not found:** use a blog owned by the authenticated account and check the blog name, without a trailing `.tumblr.com` where a tool asks for just a name.

## Alternative HTTP launcher

`run_server.py` starts an HTTP server on port 8080 and listens on all network interfaces. It is unnecessary for the local client instructions above. The included launcher does not add authentication; do not publish it through a tunnel as-is. A remote deployment needs its own access control and token storage design.

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
