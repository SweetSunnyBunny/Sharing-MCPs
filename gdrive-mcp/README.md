# Google Drive MCP

Browse and manage Drive files, create/read Docs and Sheets, and read Calendar events.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\gdrive-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\gdrive-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Connect your Google account once

You need a Google account, a browser, and permission to create a Google Cloud project. An API key alone is not enough: this package uses an **OAuth Desktop app** and a browser sign-in.

1. Open [Google Cloud Console](https://console.cloud.google.com/), create/select a project, and keep that project selected.
2. Under **APIs & Services → Library**, find and enable **Google Drive API, Google Docs API, Google Sheets API, and Google Calendar API**.
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
    "gdrive-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/gdrive-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/gdrive-mcp/server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `test_connection`. A successful response includes your connected Drive account's name and email. Then try `list_files` before creating or changing anything.

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

### Drive - Browsing
| Tool | Description |
|------|-------------|
| `test_connection` | Check connection and storage info |
| `list_files` | List files in a folder |
| `search_files` | Search for files |
| `get_file_info` | Get file details |

### Drive - Folders
| Tool | Description |
|------|-------------|
| `create_folder` | Create a new folder |

### Drive - File Operations
| Tool | Description |
|------|-------------|
| `upload_file` | Upload a file |
| `download_file` | Download a file |
| `move_file` | Move to another folder |
| `rename_file` | Rename a file |
| `trash_file` | Move to trash |
| `delete_file` | Permanently delete |

### Drive - Sharing
| Tool | Description |
|------|-------------|
| `share_file` | Share with email |
| `create_share_link` | Create shareable link |

### Google Docs
| Tool | Description |
|------|-------------|
| `create_document` | Create new Doc |
| `read_document` | Read Doc content |
| `append_to_document` | Append text |

### Google Sheets
| Tool | Description |
|------|-------------|
| `create_spreadsheet` | Create new Sheet |
| `read_spreadsheet` | Read range |
| `write_spreadsheet` | Write to range |
| `append_to_spreadsheet` | Append row |

---

## Example Usage

**"What files do I have?"**
```
Claude uses list_files() to show your Drive contents
```

**"Create a document called 'Meeting Notes'"**
```
Claude uses create_document(title="Meeting Notes")
```

**"Add this to my spreadsheet"**
```
Claude uses append_to_spreadsheet() to add a row
```

---
