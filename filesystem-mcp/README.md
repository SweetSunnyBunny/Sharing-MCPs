# Filesystem MCP

Read, search, create and manage files accessible to the account running this server. No account signup or API key is needed. This implementation has the process account's file access; it does not supply a folder allowlist.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\filesystem-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\filesystem-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Make a practice folder

```powershell
New-Item -ItemType Directory -Force "C:\MCP-Practice"
Set-Content -LiteralPath "C:\MCP-Practice\hello.txt" -Value "Hello from the filesystem test."
```

Use this folder for the first calls. Paths refer to the machine running the server. No `.env` file is used. Local stdio is the simplest connection; `run_server.py` opens an unauthenticated HTTP listener on all interfaces and is not needed for these steps.

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
    "filesystem-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/filesystem-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/filesystem-mcp/server.py"
      ]
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `fs_list_directory` for `C:/MCP-Practice`, then `fs_read_file` on `C:/MCP-Practice/hello.txt`. Seeing the test text confirms setup without modifying other files.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Access denied:** choose files your account can open; administrator privileges are not needed for the practice folder.
- **File not found:** use a path on this computer, not on your phone or another machine.
- **Image not displayed:** confirm your client supports image tool results and the image exists.

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

## Reading the code

Start with [run_server.py](run_server.py), then [server.py](server.py). The installation steps above describe the runtime configuration.
