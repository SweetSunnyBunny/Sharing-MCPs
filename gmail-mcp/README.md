# Gmail MCP

Read, search and organize Gmail, create drafts, and send messages through your own account.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\gmail-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\gmail-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Connect your Google account once

You need a Google account, a browser, and permission to create a Google Cloud project. An API key alone is not enough: this package uses an **OAuth Desktop app** and a browser sign-in.

1. Open [Google Cloud Console](https://console.cloud.google.com/), create/select a project, and keep that project selected.
2. Under **APIs & Services → Library**, find and enable **Gmail API**.
3. Open **Google Auth platform**. Complete Branding with an app name and your contact email. For a personal account choose an External audience; while the app is in Testing, add your own sign-in email as a test user. [Google's consent setup guide](https://developers.google.com/workspace/guides/configure-oauth-consent).
4. Under **Clients**, create an OAuth client with application type **Desktop app**. Download its JSON. [Google's credentials guide](https://developers.google.com/workspace/guides/create-credentials).
5. In this package folder, create the authentication directory:

```powershell
New-Item -ItemType Directory -Force .\auth
```

6. In File Explorer, copy the downloaded JSON into this package's `auth` folder. Rename it exactly `client_secret.json` (not `.json.json`). Check that the file is present before continuing.
7. Now start the login wizard:

```powershell
.\.venv\Scripts\python.exe .\setup.py
```

The setup script opens your browser. Sign in with the account added as a test user and review the requested access. Wait for **Authentication successful!** in PowerShell and `auth/token.pickle` to appear. The local callback port is chosen automatically; do not create a web-app client or invent a redirect URL.

Keep both files in `auth` private. They are generated setup inputs, not files missing from this download. No `.env` file is required. `setup.py` here means authenticate this app; it is not a command to publish/install a Python package.

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
    "gmail-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/gmail-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/gmail-mcp/server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `test_connection`. A successful response includes your connected Gmail address in `email`. Then try `list_messages` or `list_labels` before creating or sending anything.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Credentials file not found:** check `auth/client_secret.json` inside this exact package folder.
- **Access blocked/test user error:** use the account added under Audience; organizational accounts may also need administrator permission.
- **API disabled:** check that the required APIs are enabled in the same project that owns the OAuth client.
- **Expired or revoked sign-in:** run the same `setup.py` command again and complete the browser flow. It replaces the saved token.
- **Browser did not open:** run setup on a machine with a local browser and watch its terminal output; this flow is not for a headless cloud server.

For local use, start `server.py` as above. The separate `run_server.py` opens an unauthenticated HTTP listener on all interfaces; it is not needed for this stdio setup.

## Available Tools

### Reading
| Tool | Description |
|------|-------------|
| `test_connection` | Check connection and get account info |
| `list_messages` | List messages with optional search query |
| `get_message` | Get full message content |
| `get_thread` | Get all messages in a thread |
| `search` | Advanced Gmail search |

### Sending
| Tool | Description |
|------|-------------|
| `send_message` | Send an email |
| `send_with_attachment` | Send with file attachment |
| `create_draft` | Create a draft (not sent) |

### Labels
| Tool | Description |
|------|-------------|
| `list_labels` | List all labels |
| `create_label` | Create a new label |
| `modify_labels` | Add/remove labels from message |

### Actions
| Tool | Description |
|------|-------------|
| `trash_message` | Move to trash |
| `untrash_message` | Restore from trash |
| `mark_read` | Mark as read |
| `mark_unread` | Mark as unread |
| `archive_message` | Remove from inbox |

---

## Example Usage

**"Show me my unread emails"**
```
Claude uses list_messages(query="is:unread")
```

**"Send an email to bob@example.com about the meeting"**
```
Claude uses send_message(to="bob@example.com", subject="...", body="...")
```

**"Search for emails from last week with attachments"**
```
Claude uses search(query="after:2024/01/15 has:attachment")
```

---

## Gmail Search Syntax

The `query` parameter supports Gmail's search syntax:

| Operator | Example | Description |
|----------|---------|-------------|
| `from:` | `from:john@example.com` | From sender |
| `to:` | `to:me` | To recipient |
| `subject:` | `subject:meeting` | In subject |
| `is:` | `is:unread`, `is:starred` | Message status |
| `has:` | `has:attachment` | Has attachment |
| `after:` | `after:2024/01/01` | After date |
| `before:` | `before:2024/12/31` | Before date |
| `label:` | `label:important` | Has label |
| `larger:` | `larger:5M` | Larger than size |

---
