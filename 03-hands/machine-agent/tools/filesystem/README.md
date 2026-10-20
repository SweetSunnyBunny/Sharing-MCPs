# Filesystem tools — optional standalone server

If you are following [Machine Agent](../../README.md), **you do not need to run
this folder separately**. The main bridge imports `server.py` itself. Use that
guide for a single authenticated bridge shared by your tool groups.

This folder can also run a direct filesystem MCP server without Machine Agent.
The steps below are for that independent local option. It can read, edit, write,
move and delete files accessible to the Windows account that runs it.

## 1. Install the standalone option

Install Python 3.11. Open `machine-agent\tools\filesystem` in File Explorer,
type `powershell` in the address bar, press Enter, then run:

```powershell
py -3.11 --version
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\run_server.py --help
```

You should see help with `--transport`, `--host` and `--port`. Explicit `.venv`
paths mean you do not need to activate the environment.

## 2. Add it to a local MCP client

Choose a command/stdio MCP server. Use the full path to this folder's
`.venv\Scripts\python.exe` as the command, and the full path to this folder's
`run_server.py` followed by `--transport`, `stdio` as arguments. A generic example:

```json
{
  "mcpServers": {
    "filesystem-local": {
      "command": "C:/MCP-Starter/Sharing-MCPs/machine-agent/tools/filesystem/.venv/Scripts/python.exe",
      "args": ["C:/MCP-Starter/Sharing-MCPs/machine-agent/tools/filesystem/run_server.py", "--transport", "stdio"]
    }
  }
}
```

Replace both paths if you extracted elsewhere. The client starts the process;
you do not start a second copy in PowerShell. Refresh its tool list, then ask
for `fs_list_directory` on this package's full path. A directory listing is the
first successful read-only check. Running stdio by hand may simply wait for
MCP messages without printing a prompt; that is expected.

## Optional local HTTP mode

```powershell
.\.venv\Scripts\python.exe .\run_server.py --transport streamable-http --host 127.0.0.1 --port 8080
```

Keep that terminal open and connect a local HTTP MCP client to
`http://127.0.0.1:8080/mcp`. This direct server has no authentication gate. Keep
it on loopback; do not expose it through a public tunnel as-is. Use the parent
Machine Agent's authenticated setup for the separate bridge workflow.

## Tool reference

| Tools | Purpose |
|---|---|
| `fs_list_directory`, `fs_get_file_info`, `fs_list_drives` | Inspect folders, metadata and Windows drives. |
| `fs_read_file`, `fs_read_image` | Read text or a bounded image preview. |
| `fs_search`, `fs_search_content`, `fs_get_recent_files` | Find files or text. |
| `fs_create_directory`, `fs_write_file`, `fs_write_binary` | Create folders and write files. |
| `fs_edit_file` | Replace an exact text block with a match-count guard. |
| `fs_copy`, `fs_move`, `fs_delete` | Copy, move or delete files/folders. |

`MCP_TRANSPORT`, `HOST` and `PORT` are environment-variable alternatives to the
command-line options. This server has no directory sandbox beyond your Windows
account's permissions; choose file-operation requests accordingly.

## Common fixes

- **Client cannot start Python:** use the full `.venv` executable path, not `python`.
- **Missing module:** install requirements with that same `.venv` executable.
- **HTTP connection refused:** keep the HTTP terminal running and use its port.
- **Access denied:** check the Windows account's permission for the target path.

Press Ctrl+C to stop a manually launched HTTP server. License: MIT.
