# Terminal MCP

Persistent shell sessions for an MCP client: PowerShell on Windows and Bash on macOS/Linux. Commands share directory and environment within a session. After a timeout, the process tree is stopped; the next call restarts the shell and preserves the last known directory, but not its environment.

## 1. Prepare the folder and Python

These steps use Windows PowerShell and **Python 3.11 (64-bit)**. Install it from [python.org](https://www.python.org/downloads/), including the Python launcher, if needed. Reopen PowerShell after installation.

Extract the collection to `C:\MCP-Starter\Sharing-MCPs`, so this README is inside `C:\MCP-Starter\Sharing-MCPs\terminal-mcp`. If you chose another location, replace that path in every command and JSON example below. Do not run inside the ZIP.

```powershell
Set-Location "C:\MCP-Starter\Sharing-MCPs\terminal-mcp"
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`.venv` keeps this package's Python libraries together. Using its executable directly avoids installing into the wrong Python and needs no activation script or PowerShell policy change.

## 2. Choose your shell and starting directory

On Windows the default is powershell.exe. Optionally select pwsh.exe if you have
PowerShell 7 installed. The Windows implementation uses PowerShell syntax and
flags; do not point it at Git Bash or cmd.exe. macOS/Linux uses Bash.

```powershell
$env:TERMINAL_SHELL = "powershell.exe"
$env:TERMINAL_DEFAULT_CWD = "C:\MCP-Starter\Sharing-MCPs"
```

The client configuration repeats these variables because a GUI client need not
inherit this terminal's environment. No API key is required.

## 3. Check startup

```powershell
.\.venv\Scripts\python.exe .\run_server.py
```

This is a **stdio** server: a client talks through the process's input/output, not a web page. A banner followed by silence, or silence alone, is normal while it waits. A Python traceback is an error. Press **Ctrl+C** after this check, then connect your client below.

## 4. Connect your AI client and check it works

Claude Desktop: **Settings → Developer → Edit Config**. [Client connection guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers). Other clients use their own local MCP settings; this example uses `mcpServers` JSON.

```json
{
  "mcpServers": {
    "terminal-mcp": {
      "command": "C:/MCP-Starter/Sharing-MCPs/terminal-mcp/.venv/Scripts/python.exe",
      "args": [
        "C:/MCP-Starter/Sharing-MCPs/terminal-mcp/run_server.py"
      ],
      "env": {
        "TERMINAL_SHELL": "powershell.exe",
        "TERMINAL_DEFAULT_CWD": "C:/MCP-Starter/Sharing-MCPs"
      }
    }
  }
}
```

If you already have servers, add this entry inside the existing `mcpServers` object and keep the others. Forward slashes in these Windows JSON paths are intentional. Save, fully quit the client, then reopen it. The client starts Python for you; do not leave a second manual copy running.

Ask the client to call `terminal_execute` with command `Get-Location` on Windows (or `pwd` with Bash). The result should contain a directory and exit code. Then call `terminal_list` to see the session. Start with read-only commands.

## If something goes wrong

- **`py` is not recognized:** install Python with its launcher, then reopen PowerShell. If only `python` works, verify `python --version` and use it for the `-m venv` command.
- **`No module named ...`:** repeat the requirements command using `.\.venv\Scripts\python.exe`; the client's `command` must point to that same environment.
- **Server missing in the client:** check absolute paths and JSON punctuation, then restart the client. Claude Desktop logs are under `%APPDATA%\Claude\logs`.
- **A quiet terminal:** this is expected for stdio; use the client tool check above. Ctrl+C stops a manual test.
- **Bash not found:** fix `TERMINAL_SHELL` in the client JSON.
- **Unknown option or timeout:** do not point the shell setting at powershell.exe, pwsh.exe or cmd.exe.
- **Command waiting for input:** use noninteractive command flags or end that session with `terminal_destroy`.
- **Lost shell state:** sessions last only while the server runs; restarting the client starts fresh sessions.

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

## Available Tools

| Tool | Description |
|------|-------------|
| `terminal_execute` | Run a command in a persistent session |
| `terminal_create` | Create a new named session |
| `terminal_list` | List all active sessions |
| `terminal_destroy` | Kill a session and its shell process |
| `terminal_get_info` | Get detailed info about a session |

---

## Reading the code

Start with [run_server.py](run_server.py), then [terminal_server.py](terminal_server.py). The installation steps above describe the runtime configuration.
